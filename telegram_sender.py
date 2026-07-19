"""Telegram sender — build messages and send to Dr_NewPaper_bot channel."""

import json
import re
import os
import urllib.request
from typing import List, Dict
from pathlib import Path

TELEGRAM_API = "https://api.telegram.org/bot"

# Load .env
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

if BOT_TOKEN:
    TELEGRAM_API += BOT_TOKEN


def strip_parentheses(url: str) -> str:
    """Strip parentheses that Telegram might interpret as markdown links."""
    return re.sub(r"[\(\)]", "", url)


def _esc(text: str) -> str:
    """Escape Telegram MarkdownV2 reserved characters."""
    if not text:
        return text
    for ch in ['_', '*', '`', '[', ']', '(', ')', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, '\\' + ch)
    return text


def build_message(results: List[Dict], query: str) -> str:
    """Build a formatted Telegram message from search results.

    Handles both normal results (with MiniMax summary)
    and deep results (with deep_summary field).
    All user content is escaped for Telegram MarkdownV2.
    """
    if not results:
        return "❌ Aucun résultat trouvé."

    lines = []

    for i, r in enumerate(results, 1):
        title = _esc((r.get("title") or "Unknown")[:85])
        source = r.get("source", "") or ""
        date = r.get("date", "") or ""
        doi = r.get("doi", "") or ""
        authors = (r.get("authors") or [])[:3]
        journal = r.get("journal", "") or ""
        summary = r.get("summary") or ""
        deep_summary = r.get("deep_summary") or ""
        pdf_avail = r.get("pdf_available", False)
        pdf_tag = " 📄dispo" if pdf_avail else " 🔒indisponible"

        lines.append(f"{i}. {title}")
        meta_parts = [p for p in [source, date, journal] if p]
        if meta_parts:
            lines.append(f"   📖 {' | '.join(meta_parts)}")

        if authors:
            lines.append(f"   👥 {_esc(', '.join(authors))}")

        if doi:
            lines.append(f"   🔑 DOI: {doi}{pdf_tag}")

        # Summary — prefer deep_summary, both escaped
        if deep_summary:
            lines.append(f"\n{_esc(deep_summary)}\n")
        elif summary:
            safe_summary = _esc(summary[:200])
            lines.append(f"\n{safe_summary}{'...' if len(summary) > 200 else ''}\n")
        elif r.get("abstract"):
            abstract = re.sub(r"<[^>]+>", "", r.get("abstract", ""))
            safe_abs = _esc(abstract[:180])
            lines.append(f"\n_{safe_abs}..._\n")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

    return "\n".join(lines)


def build_telegram_file(results: List[Dict], query: str, domain: str,
                         deep: bool = False) -> str:
    """Build a detailed file-ready message (used for file output)."""
    lines = []
    lines.append(f"# Research Results — {query}")
    lines.append(f"Domain: {domain} | Results: {len(results)}")
    lines.append("=" * 60)
    lines.append("")

    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r.get('title', 'Unknown')}")
        lines.append(f"- Source: {r.get('source', '')}")
        lines.append(f"- Date: {r.get('date', 'N/A')}")
        lines.append(f"- Authors: {', '.join(r.get('authors', [])[:5])}")
        lines.append(f"- DOI: {r.get('doi', 'N/A')}")
        lines.append(f"- Journal: {r.get('journal', 'N/A')}")
        lines.append(f"- URL: {r.get('url', 'N/A')}")
        pdf_avail = r.get("pdf_available", False)
        lines.append(f"- PDF: {'Disponible' if pdf_avail else 'Indisponible'}")

        if r.get("deep_summary"):
            lines.append(f"\n### Deep Summary ({r.get('summary_method', '')})")
            lines.append(r["deep_summary"])
        elif r.get("summary"):
            lines.append(f"\n### Summary")
            lines.append(r["summary"])
        elif r.get("abstract"):
            lines.append(f"\n### Abstract")
            lines.append(r["abstract"])

        lines.append("")
        lines.append("-" * 60)
        lines.append("")

    return "\n".join(lines)


def send_results(results: List[Dict], query: str, domain: str = "") -> Dict:
    """Send results to Telegram channel. Returns status dict.

    ``domain`` is optional context (e.g. the research domain run.py searched);
    when present it is prepended as a header line.
    """
    if not BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {"sent": False, "error": "Telegram credentials not configured"}

    message = build_message(results, query)
    if domain:
        message = f"🔬 {domain}\n\n{message}"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    try:
        req = urllib.request.Request(
            f"{TELEGRAM_API}/sendMessage",
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return {"sent": True, "message_id": result["result"]["message_id"]}
            return {"sent": False, "error": result}
    except Exception as e:
        return {"sent": False, "error": str(e)}


def send_text_to_telegram(text: str, chat_id: str = None) -> Dict:
    """Send a raw text message to Telegram."""
    if not BOT_TOKEN:
        return {"sent": False, "error": "No bot token"}

    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        return {"sent": False, "error": "No chat_id"}

    payload = {
        "chat_id": target,
        "text": text,
        "disable_web_page_preview": True,
    }

    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    try:
        req = urllib.request.Request(
            f"{TELEGRAM_API}/sendMessage",
            data=data,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {"sent": result.get("ok", False), "result": result}
    except Exception as e:
        return {"sent": False, "error": str(e)}
