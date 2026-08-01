"""PDF Sender — download PDFs and send to Telegram chat.

Workflow:
1. Try OpenAlex OA PDF (free, legal)
2. Try Semantic Scholar OA PDF (free, legal)
3. Try CORE.ac.uk direct download (free, legal)
4. Fall back to Sci-Hub (if really needed — see scihub.py note)

Telegram limits:
- Max file size: 50 MB per document
- Supported: PDF, ZIP, MP3 (max 50 MB each)

Usage:
    from pdf_sender import download_pdf, send_pdf_to_telegram, download_and_send
"""

import os
import re
import time
import json
import logging
import urllib.request
import urllib.parse
from typing import Dict, Optional
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Load .env
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

PDF_CACHE_DIR = "/tmp/drnewpaper_pdfs"
os.makedirs(PDF_CACHE_DIR, exist_ok=True)

# User-visible library so downloaded PDFs are actually reachable on disk
# (the /tmp cache is volatile). Overridable via env for tests / custom layouts.
PDF_LIBRARY_DIR = os.getenv(
    "DR_NEWPAPER_PDF_DIR",
    str(config.DOSSIER_BASE / "pdfs"),
)


def _scihub_allowed(explicit: Optional[bool]) -> bool:
    """Resolve the Sci-Hub fallback at call time (never trust an import-time const).

    Delegates to :func:`config.scihub_enabled`: Sci-Hub is opt-in, so an
    unspecified caller does *not* get it unless an operator opted in via
    ``DR_NEWPAPER_ALLOW_SCIHUB=1``.
    """
    from config import scihub_enabled
    return scihub_enabled(explicit)

# In-memory message ID cache to prevent duplicate sends
# Format: {doi: {"path": str, "message_ids": [int, ...], "timestamp": float}}
_sent_cache: Dict[str, Dict] = {}
_CACHE_MAX_AGE = 3600  # 1 hour


def _sanitize_filename(title: str) -> str:
    """Make a safe filename from a paper title."""
    # Remove special chars, keep alphanumeric + spaces
    safe = re.sub(r'[<>:"/\\|?*]', '', title)
    safe = safe.strip()[:80]
    return safe or "paper"


def _is_already_cached(doi: str) -> Optional[Dict]:
    """Return cached entry if fresh, else None."""
    if doi not in _sent_cache:
        return None
    entry = _sent_cache[doi]
    if time.time() - entry.get("timestamp", 0) > _CACHE_MAX_AGE:
        del _sent_cache[doi]
        return None
    return entry


def _cache_result(doi: str, path: str, message_ids: list):
    """Store successful send in cache."""
    _sent_cache[doi] = {
        "path": path,
        "message_ids": message_ids,
        "timestamp": time.time(),
    }


def _is_valid_pdf(filepath: str) -> bool:
    """Check if file is actually a PDF (not HTML or other content)."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(5)
        return header.startswith(b"%PDF")
    except Exception:
        return False


def _download_url_to(url: str, filepath: str) -> bool:
    """Download a single URL to `filepath` and verify it's a real PDF.

    Handles the common arXiv `/abs/` → `/pdf/` quirk. Returns True on success.
    """
    if not url:
        return False
    if "arxiv.org/abs/" in url:
        url = url.replace("/abs/", "/pdf/")
        if not url.endswith(".pdf"):
            url += ".pdf"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "application/pdf,application/octet-stream,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read()
        if len(content) < 5000:
            return False
        with open(filepath, "wb") as f:
            f.write(content)
        return _is_valid_pdf(filepath)
    except Exception:
        logger.debug("Direct PDF download failed: %s", url, exc_info=True)
        return False


def _try_known_urls(known_urls: list, filepath: str) -> bool:
    """Try the PDF/landing URLs we already harvested during search (no API call)."""
    for url in known_urls or []:
        if url and _download_url_to(url, filepath):
            return True
    return False


def _try_unpaywall_pdf(doi: str, filepath: str) -> bool:
    """Look the DOI up on Unpaywall (free, no key) and download its OA PDF."""
    try:
        from sources.unpaywall import get_oa_pdf_url
        info = get_oa_pdf_url(doi)
        url = info.get("best_url", "") if isinstance(info, dict) else ""
        return _download_url_to(url, filepath)
    except Exception:
        logger.debug("Unpaywall PDF download failed for %s", doi, exc_info=True)
        return False


def _try_openalex_pdf(doi: str, filepath: str) -> bool:
    """Try downloading from OpenAlex OA PDF."""
    try:
        from sources.openalex import get_by_doi as oa_get_by_doi
        item = oa_get_by_doi(doi)
        pdf_url = item.get("oa_pdf_url", "") if isinstance(item, dict) else ""
        if not pdf_url:
            return False
        req = urllib.request.Request(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read()
            if len(content) < 5000:
                return False
            with open(filepath, "wb") as f:
                f.write(content)
            return _is_valid_pdf(filepath)
    except Exception:
        logger.debug("OpenAlex PDF download failed for %s", doi, exc_info=True)
        return False


def _try_semantic_pdf(doi: str, filepath: str) -> bool:
    """Try downloading from Semantic Scholar OA PDF."""
    try:
        from sources.semantic_scholar import get_by_doi as ss_get_by_doi
        item = ss_get_by_doi(doi)
        pdf_url = ""
        if isinstance(item, dict):
            pdf_url = item.get("oa_pdf_url", "")
        if not pdf_url:
            return False
        req = urllib.request.Request(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read()
            if len(content) < 5000:
                return False
            with open(filepath, "wb") as f:
                f.write(content)
            return _is_valid_pdf(filepath)
    except Exception:
        logger.debug("Semantic Scholar PDF download failed for %s", doi, exc_info=True)
        return False


def _try_core_pdf(doi: str, filepath: str) -> bool:
    """Try downloading from CORE.ac.uk direct PDF."""
    try:
        from sources.core_ac import get_by_doi as core_get_by_doi
        item = core_get_by_doi(doi)
        pdf_url = ""
        if isinstance(item, dict):
            pdf_url = item.get("oa_pdf_url", "")
        if not pdf_url:
            return False
        # arXiv returns /abs/ page, not PDF — convert to /pdf/
        if "arxiv.org/abs/" in pdf_url:
            pdf_url = pdf_url.replace("/abs/", "/pdf/") + ".pdf"
        req = urllib.request.Request(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read()
            if len(content) < 5000:
                return False
            with open(filepath, "wb") as f:
                f.write(content)
            return _is_valid_pdf(filepath)
    except Exception:
        logger.debug("CORE PDF download failed for %s", doi, exc_info=True)
        return False


def _try_scihub_pdf(doi: str, filepath: str) -> bool:
    """Fall back to Sci-Hub for paywalled PDFs."""
    try:
        from sources.scihub import try_download_pdf as scihub_download
        result = scihub_download(doi, output_dir=PDF_CACHE_DIR, allow_scihub=True)
        if result.get("success") and result.get("path"):
            # Copy to our preferred filename
            import shutil
            scihub_path = result["path"]
            size = os.path.getsize(scihub_path)
            if size > 5000 and _is_valid_pdf(scihub_path):
                shutil.copy(scihub_path, filepath)
                return True
        return False
    except Exception:
        logger.debug("Sci-Hub PDF download failed for %s", doi, exc_info=True)
        return False


def _publish_to_library(src_path: str, title: str, doi: str) -> str:
    """Copy a downloaded PDF into the user-visible library; return its path."""
    try:
        os.makedirs(PDF_LIBRARY_DIR, exist_ok=True)
        safe_title = _sanitize_filename(title) or "paper"
        suffix = (doi or "").replace("/", "_") or "nodoi"
        dest = os.path.join(PDF_LIBRARY_DIR, f"{safe_title}_{suffix}.pdf")
        if os.path.abspath(dest) != os.path.abspath(src_path):
            import shutil
            shutil.copy(src_path, dest)
        return dest
    except Exception:
        logger.debug("Could not publish PDF to library", exc_info=True)
        return src_path


def download_pdf(doi: str, title: str = "", known_urls: Optional[list] = None,
                 allow_scihub: Optional[bool] = None) -> Dict:
    """Download a PDF for an article.

    Strategy (cheapest/most-legal first):
        known URLs (already harvested) → Unpaywall → OpenAlex → Semantic Scholar
        → CORE → Sci-Hub (only if allowed)

    ``known_urls`` lets the caller pass the ``oa_pdf_url`` / ``pdf_url`` / ``url``
    it already has, so we don't re-query an API for something we know. A DOI is
    no longer strictly required — if we have a direct PDF URL we use it.
    ``allow_scihub`` is resolved at call time (see ``_scihub_allowed``): Sci-Hub
    is opt-in, so an unspecified caller gets the open-access methods only unless
    an operator opted in via ``DR_NEWPAPER_ALLOW_SCIHUB=1``.

    Returns dict with: success, path, method, error
    """
    doi_clean = (doi or "").strip()
    known_urls = [u for u in (known_urls or []) if u]
    if not doi_clean and not known_urls:
        return {"success": False, "path": "", "method": "", "error": "No DOI or PDF URL provided"}

    safe_title = _sanitize_filename(title)
    key = doi_clean.replace("/", "_") if doi_clean else _sanitize_filename(known_urls[0])[:40]
    pdf_path = os.path.join(PDF_CACHE_DIR, f"{safe_title}_{key}.pdf")

    # Already downloaded and cached?
    if os.path.exists(pdf_path) and _is_valid_pdf(pdf_path):
        return {"success": True, "path": pdf_path, "method": "cache",
                "library_path": _publish_to_library(pdf_path, title, doi_clean), "error": ""}

    methods = [("known_url", lambda: _try_known_urls(known_urls, pdf_path))]
    if doi_clean:
        methods += [
            ("unpaywall", lambda: _try_unpaywall_pdf(doi_clean, pdf_path)),
            ("openalex", lambda: _try_openalex_pdf(doi_clean, pdf_path)),
            ("semantic", lambda: _try_semantic_pdf(doi_clean, pdf_path)),
            ("core", lambda: _try_core_pdf(doi_clean, pdf_path)),
        ]
    if doi_clean and _scihub_allowed(allow_scihub):
        methods.append(("scihub", lambda: _try_scihub_pdf(doi_clean, pdf_path)))

    for method_name, fn in methods:
        try:
            if fn() and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 5000:
                return {"success": True, "path": pdf_path, "method": method_name,
                        "library_path": _publish_to_library(pdf_path, title, doi_clean), "error": ""}
        except Exception:
            continue

    tried = ", ".join(m for m, _ in methods)
    scihub_hint = "" if _scihub_allowed(allow_scihub) else " (repli Sci-Hub non activé : DR_NEWPAPER_ALLOW_SCIHUB=1 pour l'autoriser)"
    return {
        "success": False,
        "path": "",
        "method": "",
        "error": f"Aucun PDF trouvé via : {tried}{scihub_hint}",
    }


def _send_file_to_telegram(filepath: str, filename: str, caption: str = "", chat_id: str = None) -> Dict:
    """Send a file to Telegram using multipart/form-data.

    Returns dict with: success, message_id, error
    """
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not BOT_TOKEN or not target_chat:
        return {"success": False, "message_id": None, "error": "Telegram credentials missing"}

    if not os.path.exists(filepath):
        return {"success": False, "message_id": None, "error": f"File not found: {filepath}"}

    filesize = os.path.getsize(filepath)
    if filesize < 1000:
        return {"success": False, "message_id": None, "error": "File too small"}

    if filesize > 50 * 1024 * 1024:
        return {"success": False, "message_id": None, "error": "File exceeds 50 MB Telegram limit"}

    try:
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        with open(filepath, "rb") as f:
            file_content = f.read()

        body = f"--{boundary}\r\n"
        body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        body += f"{target_chat}\r\n"

        if caption:
            body += f"--{boundary}\r\n"
            body += f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            body += f"{caption[:1024]}\r\n"

        body += f"--{boundary}\r\n"
        body += f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        body += f"Content-Type: application/pdf\r\n\r\n"

        # Encode everything properly
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        body_bytes += file_content
        body_bytes += f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            f"{TELEGRAM_API}/sendDocument",
            data=body_bytes,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return {
                    "success": True,
                    "message_id": result["result"]["message_id"],
                    "error": "",
                }
            return {"success": False, "message_id": None, "error": str(result)}
    except Exception as e:
        return {"success": False, "message_id": None, "error": str(e)}


def download_and_send(doi: str, title: str = "", chat_id: str = None) -> Dict:
    """Download PDF by DOI and send it to Telegram.

    Returns dict with: success, path, message_id, method, error
    """
    if not chat_id:
        chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return {"success": False, "path": "", "message_id": None, "method": "", "error": "No chat_id"}

    # Check cache first
    cached = _is_already_cached(doi)
    if cached and os.path.exists(cached["path"]):
        # Already sent — re-send from cache
        safe_title = _sanitize_filename(title)
        filename = f"{safe_title}.pdf"
        caption = f"📄 {title[:200]}" if title else "📄 PDF"
        result = _send_file_to_telegram(cached["path"], filename, caption, chat_id)
        return {
            **result,
            "path": cached["path"],
            "method": "cache",
        }

    # Download
    dl = download_pdf(doi, title=title)
    if not dl["success"]:
        return {**dl, "message_id": None}

    pdf_path = dl["path"]
    method = dl["method"]

    # Send to Telegram
    safe_title = _sanitize_filename(title)
    filename = f"{safe_title}.pdf"
    caption = f"📄 {title[:200]}\n🔗 DOI: {doi}" if title else f"📄 PDF — DOI: {doi}"

    result = _send_file_to_telegram(pdf_path, filename, caption, chat_id)
    if result["success"]:
        _cache_result(doi, pdf_path, [result["message_id"]])

    return {
        **result,
        "path": pdf_path,
        "method": method,
    }