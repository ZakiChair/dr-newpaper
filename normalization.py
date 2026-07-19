"""Article normalization and metadata merging for Dr_NewPaper."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Mapping

_DOI_RE = re.compile(r"(10\.\d{4,9}/\S+)", re.I)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().strip(".。")
    text = text.replace("https://doi.org/", "").replace("http://doi.org/", "")
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    match = _DOI_RE.search(text)
    if match:
        text = match.group(1)
    return text.strip().strip(".。 /").lower()


def normalize_title(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


def _slug(text: str, limit: int = 80) -> str:
    text = _NON_WORD.sub("_", text.lower()).strip("_")
    return text[:limit].strip("_") or "untitled"


def publication_year(article: Mapping[str, Any]) -> str:
    for key in ("publication_date", "date", "year"):
        raw = str(article.get(key) or "")
        match = re.search(r"(19|20)\d{2}", raw)
        if match:
            return match.group(0)
    return "unknown"


def canonical_article_key(article: Mapping[str, Any]) -> str:
    doi = normalize_doi(article.get("doi"))
    if doi:
        return f"doi:{doi}"
    title = _slug(normalize_title(article.get("title")))
    authors = article.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    first_author = _slug(str(authors[0])) if authors else "unknown"
    return f"title:{publication_year(article)}:{first_author}:{title}"


def normalize_article(article: Mapping[str, Any]) -> dict:
    authors = article.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(",") if a.strip()]
    sources = article.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    source = article.get("source") or "unknown"
    if source and source not in sources:
        sources.append(source)
    date = str(article.get("publication_date") or article.get("date") or article.get("year") or "")
    return {
        **dict(article),
        "doi": normalize_doi(article.get("doi")),
        "title": str(article.get("title") or "Untitled").strip(),
        "abstract": str(article.get("abstract") or ""),
        "authors": [str(a) for a in authors if a],
        "source": str(source),
        "sources": list(dict.fromkeys([str(s) for s in sources if s])),
        "publication_date": date,
        "year": publication_year({**article, "publication_date": date}),
        "canonical_id": canonical_article_key(article),
    }


def _better(current: Any, incoming: Any) -> Any:
    if incoming is None or incoming == "":
        return current
    if current is None or current == "":
        return incoming
    if isinstance(current, str) and isinstance(incoming, str) and len(incoming) > len(current):
        return incoming
    return current


def merge_article_records(current: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict:
    left = normalize_article(current)
    right = normalize_article(incoming)
    merged = dict(left)
    for key in {
        "title", "abstract", "date", "publication_date", "url", "journal", "type", "pmid",
        "doi", "oa_pdf_url", "pdf_url", "summary", "deep_summary", "summary_method",
        "citation_count", "oa_status", "license", "oa_version",
    }:
        merged[key] = _better(merged.get(key), right.get(key))
    merged["authors"] = list(dict.fromkeys((left.get("authors") or []) + (right.get("authors") or [])))[:20]
    merged["sources"] = list(dict.fromkeys((left.get("sources") or []) + (right.get("sources") or [])))
    merged["source"] = merged["sources"][0] if merged["sources"] else merged.get("source", "unknown")
    merged["doi"] = normalize_doi(merged.get("doi"))
    merged["canonical_id"] = canonical_article_key(merged)
    merged["year"] = publication_year(merged)
    return merged


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
