"""Export research dossiers for Dr_NewPaper."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable, Mapping, Any


def _authors(article: Mapping[str, Any]) -> list[str]:
    raw = article.get("authors_json") or article.get("authors") or []
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            if isinstance(value, list):
                return [str(x) for x in value]
        except Exception:
            return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower() or "article"


_BIB_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _bib_escape(value: Any) -> str:
    """Escape LaTeX/BibTeX specials so titles with %, &, _, {} can't corrupt the .bib.

    Mapped char-by-char so the backslash replacement never re-escapes the
    replacements it produces.
    """
    return "".join(_BIB_ESCAPES.get(ch, ch) for ch in str(value))


def _bib_key(article: Mapping[str, Any]) -> str:
    authors = _authors(article)
    first = authors[0].split()[-1] if authors else "unknown"
    year = str(article.get("publication_date") or article.get("year") or "0000")[:4]
    return _slug(f"{first}_{year}_{str(article.get('title',''))[:24]}")


def export_research_dossier(output_dir: str | Path, query: str, articles: Iterable[Mapping[str, Any]], since: str = "") -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = [dict(a) for a in articles]

    report = out / "report.md"
    lines = [f"# Dr_NewPaper Research Dossier — {query}", "", f"Période: {since or 'all'}", f"Articles: {len(rows)}", "", "## Top signals", ""]
    for idx, a in enumerate(rows[:20], 1):
        lines.append(f"{idx}. **{a.get('label') or 'WATCH'} {a.get('final_score') or 0}** — {a.get('title','Untitled')}")
        lines.append(f"   - DOI: {a.get('doi') or '—'}")
        lines.append(f"   - Source: {a.get('source') or '—'} | Journal: {a.get('journal') or '—'} | Date: {a.get('publication_date') or a.get('date') or '—'}")
        if a.get("abstract"):
            lines.append(f"   - Abstract: {str(a.get('abstract'))[:500]}…")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    csv_path = out / "evidence_table.csv"
    fields = ["id", "label", "final_score", "evidence_score", "risk_score", "title", "doi", "journal", "publication_date", "source", "url"]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for a in rows:
            writer.writerow({k: a.get(k, "") for k in fields})

    bib = out / "bibliography.bib"
    bib_lines = []
    for a in rows:
        authors = " and ".join(_bib_escape(x) for x in _authors(a))
        year = str(a.get("publication_date") or a.get("year") or "")[:4]
        bib_lines.append(f"@article{{{_bib_key(a)},")
        bib_lines.append(f"  title = {{{_bib_escape(a.get('title', 'Untitled'))}}},")
        if authors:
            bib_lines.append(f"  author = {{{authors}}},")
        if year:
            bib_lines.append(f"  year = {{{_bib_escape(year)}}},")
        if a.get("journal"):
            bib_lines.append(f"  journal = {{{_bib_escape(a.get('journal'))}}},")
        if a.get("doi"):
            bib_lines.append(f"  doi = {{{_bib_escape(a.get('doi'))}}},")
        if a.get("url"):
            bib_lines.append(f"  url = {{{_bib_escape(a.get('url'))}}},")
        bib_lines.append("}\n")
    bib.write_text("\n".join(bib_lines), encoding="utf-8")

    json_path = out / "articles.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return {"report": str(report), "csv": str(csv_path), "bibtex": str(bib), "json": str(json_path)}
