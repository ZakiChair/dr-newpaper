"""Write research results to markdown files."""

import os
import uuid
from pathlib import Path
from typing import List, Dict
from datetime import datetime

# Resolved from this file's own location so the repo stays movable. The former
# absolute path pointed into /tmp, which the OS wipes on reboot.
RECHERCHE_BASE = Path(__file__).resolve().parent / "Dossier"

# User-visible library for compiled meta-analysis documents. Overridable via env
# (tests point this at a tmp dir; never write into the user's real Dossier).
META_LIBRARY_DIR = Path(
    os.getenv("DR_NEWPAPER_META_DIR", str(RECHERCHE_BASE / "meta"))
)


def sanitize_query(query: str) -> str:
    """Sanitize query string for filename."""
    return query.strip().replace(" ", "_").replace("/", "_").replace(":", "_")


def write_meta_markdown(query: str, document_md: str, created_at: str = "",
                        base: "str | Path | None" = None) -> str:
    """Write a compiled meta-analysis document to ``<base>/<date>_<slug>.md``.

    Returns the written path. ``base`` defaults to ``META_LIBRARY_DIR`` (resolved
    at call time so an env override / test monkeypatch is honoured). ``created_at``
    (an ISO timestamp) seeds the filename so the file and its DB row line up; a
    short hash of it disambiguates same-day runs of the same query.
    """
    folder = Path(base) if base is not None else Path(
        os.getenv("DR_NEWPAPER_META_DIR", str(META_LIBRARY_DIR))
    )
    folder.mkdir(parents=True, exist_ok=True)

    stamp = (created_at or datetime.now().isoformat())
    date_str = stamp[:10] if len(stamp) >= 10 else datetime.now().strftime("%Y-%m-%d")
    safe_query = sanitize_query(query)[:40] or "meta"
    # A random suffix guarantees uniqueness even for the same query compiled twice
    # in one second (the stored documents differ), so no file is ever overwritten.
    suffix = uuid.uuid4().hex[:8]
    path = folder / f"{date_str}_{safe_query}_{suffix}.md"
    path.write_text(document_md or "", encoding="utf-8")
    return str(path)


def article_to_markdown(article: Dict, query: str) -> str:
    """Convert article dict to markdown."""
    lines = [
        f"# {article.get('title', 'Unknown Title')}",
        "",
        f"**Source:** {article.get('source', '?')}  ",
        f"**Date:** {article.get('date', '?')[:10]}  ",
        f"**Authors:** {', '.join(article.get('authors', [])[:5]) or 'Unknown'}  ",
        "",
        f"## Abstract",
        article.get("abstract", "Abstract not available."),
        "",
        f"## Summary",
        article.get("summary", "Summary not available."),
        "",
        f"## Links",
        f"- [Article]({article.get('url', '#')})",
    ]
    if article.get("doi"):
        lines.append(f"- [DOI](https://doi.org/{article['doi']})")
    if article.get("pdf_url"):
        lines.append(f"- [PDF (Sci-Hub)]({article['pdf_url']})")

    lines.append("")
    return "\n".join(lines)


def write_results(results: List[Dict], query: str) -> List[str]:
    """Write each article to RECHERCHE_BASE/YYYY-MM-DD_Query_PaperN.md.

    Returns list of written file paths.
    """
    folder = RECHERCHE_BASE
    folder.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_query = sanitize_query(query)[:30]
    written = []

    for i, article in enumerate(results, 1):
        filename = f"{date_str}_{safe_query}_paper_{i}.md"
        filepath = folder / filename

        content = article_to_markdown(article, query)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(str(filepath))

    # Write index
    index_path = folder / f"{date_str}_{safe_query}_index.md"
    index_lines = [
        f"# Index: {query}",
        f"**Date:** {datetime.now().isoformat()}  ",
        f"**Articles:** {len(results)}",
        "",
        "## Articles",
        "",
    ]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Unknown")[:80]
        index_lines.append(f"{i}. [{title}]({date_str}_{safe_query}_paper_{i}.md) — {r.get('source','?')}")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))

    return written