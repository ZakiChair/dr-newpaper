"""Sci-Hub source — download PDFs from DOI.

Sci-Hub provides access to paywalled PDFs using DOIs.
This module tries OA PDF first, then falls back to Sci-Hub.

Legal note: Sci-Hub operates in a legal gray area depending on jurisdiction.
Use responsibly and for legitimate research purposes only.
For --deep mode, we prioritize Open Access PDFs and only fall back to
Sci-Hub when no free option is available.
"""

import urllib.request
import urllib.parse
import os
import time
import re
import hashlib
import logging
from typing import List, Dict, Optional

from config import scihub_enabled

logger = logging.getLogger(__name__)

SCI_HUB_URLS = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
    "https://sci-hub.mw",
    "https://sci-hub.wf",
]


def get_scihub_url() -> str:
    """Return the first responsive Sci-Hub URL (200 OK)."""
    for url in SCI_HUB_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            continue
    # Last resort: return the first one anyway (caller will handle failure)
    return SCI_HUB_URLS[0]


def get_pdf_link_from_scihub(doi: str) -> Optional[str]:
    """Try to get direct PDF link from Sci-Hub for a DOI.

    This fetches the Sci-Hub page and extracts the PDF link.
    Returns the PDF URL or None if not found.
    """
    base_url = get_scihub_url()
    page_url = f"{base_url}/{doi}"

    try:
        req = urllib.request.Request(
            page_url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Referer": base_url,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        logger.debug("Sci-Hub page fetch failed", exc_info=True)
        return None

    # Extract PDF URL from the page
    # Sci-Hub stores PDF link in citation_pdf_url meta tag (relative path)
    # or in various JavaScript patterns (onclick, document.location, etc.)
    patterns = [
        r'citation_pdf_url[^>]+content=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'content=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'https?://[^\"\'\\]+\.pdf',
        r'document\.location\.href\s*=\s*["\']([^"\']+)["\']',
        r'download\.pdf["\']?\s*,\s*["\']([^"\']+)["\']',
r'iframe.*?src\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches:
            pdf_url = match if isinstance(match, str) else match[0]
            # If relative path (starts with /storage/), build full URL
            if pdf_url.startswith("/storage/"):
                pdf_url = base_url + pdf_url
            if pdf_url.startswith("http"):
                return pdf_url

    return None


def try_download_pdf(doi: str, output_dir: str = "/tmp/scihub_pdfs",
                     try_openalex_first: bool = True,
                     allow_scihub: Optional[bool] = None) -> Dict:
    """Attempt to download a PDF for a given DOI.

    Strategy:
    1. If try_openalex_first: try OpenAlex OA PDF first (free, legal)
    2. Fall back to Sci-Hub

    ``allow_scihub`` is resolved at *call* time via :func:`config.scihub_enabled`:
    Sci-Hub is the always-on fallback, so an unspecified (``None``) caller gets it
    enabled unless an operator opts out with ``DR_NEWPAPER_ALLOW_SCIHUB=0``.

    Returns dict with: success, path, url, method, error
    """
    allow_scihub = scihub_enabled(allow_scihub)
    os.makedirs(output_dir, exist_ok=True)

    # Sanitize DOI for filename
    safe_doi = doi.replace("/", "_").replace(".", "_")
    filename = os.path.join(output_dir, f"{safe_doi}.pdf")

    # Already downloaded?
    if os.path.exists(filename):
        size = os.path.getsize(filename)
        if size > 1000:  # sanity check
            return {"success": True, "path": filename, "url": "", "method": "cache", "error": ""}

    # Method 1: OpenAlex OA PDF
    if try_openalex_first:
        oa_result = _try_oa_pdf(doi, filename)
        if oa_result["success"]:
            return oa_result

    # Method 2: Sci-Hub (explicit opt-in only)
    if allow_scihub:
        scihub_result = _try_scihub_pdf(doi, filename)
        if scihub_result["success"]:
            return scihub_result
    else:
        return {
            "success": False,
            "path": "",
            "url": "",
            "method": "",
            "error": "No open-access PDF found; Sci-Hub fallback disabled via DR_NEWPAPER_ALLOW_SCIHUB",
        }

    return {
        "success": False,
        "path": "",
        "url": f"{get_scihub_url()}/{doi}",
        "method": "",
        "error": "All PDF sources failed",
    }


def _try_oa_pdf(doi: str, filename: str) -> Dict:
    """Try downloading via OpenAlex's OA PDF link."""
    try:
        from sources.openalex import get_by_doi as oa_get_by_doi
        item = oa_get_by_doi(doi)
        pdf_url = item.get("oa_pdf_url", "")
    except Exception:
        logger.debug("OpenAlex OA lookup failed for %s", doi, exc_info=True)
        pdf_url = ""

    if not pdf_url:
        return {"success": False, "path": "", "url": "", "method": "openalex", "error": "No OA PDF URL"}

    try:
        req = urllib.request.Request(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            if len(content) < 1000:
                return {"success": False, "path": "", "url": pdf_url, "method": "openalex", "error": "File too small"}
            with open(filename, "wb") as f:
                f.write(content)
        return {"success": True, "path": filename, "url": pdf_url, "method": "openalex", "error": ""}
    except Exception as e:
        return {"success": False, "path": "", "url": pdf_url, "method": "openalex", "error": str(e)}


def _is_valid_pdf(filepath: str) -> bool:
    """Return True if file starts with %PDF magic bytes."""
    try:
        with open(filepath, "rb") as f:
            return f.read(5).startswith(b"%PDF")
    except Exception:
        return False


def _try_scihub_pdf(doi: str, filename: str) -> Dict:
    """Try downloading via Sci-Hub using the extracted PDF storage URL.

    Strategy:
    1. Fetch the Sci-Hub page for the DOI and extract the /storage/ URL
    2. Download the PDF directly from that storage URL (bypasses session issues)
    3. Verify the downloaded content is actually a PDF
    """
    base_url = get_scihub_url()

    # Step 1: get the actual PDF storage URL from the Sci-Hub page
    pdf_link = get_pdf_link_from_scihub(doi)
    if not pdf_link:
        return {"success": False, "path": "", "url": f"{base_url}/{doi}", "method": "scihub", "error": "No PDF link extracted from Sci-Hub page"}

    # Step 2: download from the storage URL (strip any #fragment)
    storage_url = pdf_link.split("#")[0]
    try:
        req = urllib.request.Request(
            storage_url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Referer": f"{base_url}/{doi}",
                "Accept": "application/pdf,application/octet-stream,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            if len(content) < 1000:
                return {"success": False, "path": "", "url": storage_url, "method": "scihub", "error": "File too small or empty"}
            with open(filename, "wb") as f:
                f.write(content)
        # Verify it's actually a PDF, not HTML
        if not _is_valid_pdf(filename):
            os.remove(filename)
            return {"success": False, "path": "", "url": storage_url, "method": "scihub", "error": "Downloaded content is not a PDF (blocked or CAPTCHA)"}
        return {"success": True, "path": filename, "url": storage_url, "method": "scihub", "error": ""}
    except Exception as e:
        return {"success": False, "path": "", "url": storage_url, "method": "scihub", "error": str(e)}


def parse_pdf_for_summary(filepath: str) -> str:
    """Extract text from PDF for use in LLM summarization.

    Tries in order:
    1. marker-pdf (best quality, GPU/CPU, produces markdown)
    2. PyMuPDF (fallback, raw text extraction)
    3. pdfminer.six (last resort)

    Returns extracted text (up to ~50k chars for token budget).
    """
    if not os.path.exists(filepath):
        return ""

    filesize = os.path.getsize(filepath)
    if filesize < 1000:
        return ""

    text = ""

    # Try marker-pdf first (best quality)
    text = _parse_marker_pdf(filepath)

    # Fall back to PyMuPDF
    if not text or len(text) < 200:
        text = _parse_pymupdf(filepath)

    # Last resort: pdfminer
    if not text or len(text) < 200:
        text = _parse_pdfminer(filepath)

    # Truncate to ~50k chars to fit in LLM context
    if len(text) > 50000:
        text = text[:50000] + "\n\n[... document truncated ...]"

    return text.strip()


def _parse_marker_pdf(filepath: str) -> str:
    """Use marker-pdf (pdf2txt) to extract text/markdown from PDF."""
    try:
        import subprocess
        result = subprocess.run(
            ["pdf2txt", "-o", "markdown", filepath],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except FileNotFoundError:
        pass  # marker-pdf not installed
    except Exception:
        pass
    return ""


def _parse_pymupdf(filepath: str) -> str:
    """Use PyMuPDF (fitz) to extract raw text from PDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        try:
            import pymupdf as fitz
        except ImportError:
            return ""

    try:
        doc = fitz.open(filepath)
        parts = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                parts.append(text)
        doc.close()
        return "\n\n".join(parts)
    except Exception:
        return ""


def _parse_pdfminer(filepath: str) -> str:
    """Use pdfminer.six to extract text from PDF."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        return ""

    try:
        return extract_text(filepath, maxpages=20)
    except Exception:
        return ""


def search_scihub(doi: str = None, title: str = None) -> Dict:
    """Legacy function — returns PDF URL info for a DOI.

    DEPRECATED: Use try_download_pdf() for actual downloads.
    """
    if not doi and not title:
        return {"error": "Either DOI or title required"}

    if doi:
        pdf_url = f"{get_scihub_url()}/{doi}"
    else:
        pdf_url = f"{get_scihub_url()}/?request={urllib.parse.quote(title)}"

    return {
        "source": "Sci-Hub",
        "title": title or doi,
        "pdf_url": pdf_url,
        "note": "Prioritize OA PDFs — use try_download_pdf() instead",
    }