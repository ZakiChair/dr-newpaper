"""Semantic Scholar source — free API with excellent medical coverage.
API docs: https://api.semanticscholar.org
IMPORTANT: Requires API key (free tier: 100 req/min with key, 1 req/sec without).
Set SEMANTIC_SCHOLAR_API_KEY in .env to use this source.
"""

import os
import urllib.request
import urllib.parse
import json
import time
from pathlib import Path
from typing import List, Dict

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"

# Fields we need
PAPER_FIELDS = (
    "title,abstract,year,authors.name,venue,journal,externalIds,openAccessPdf,"
    "url,publicationDate,citationCount,inflatedCitationCount,fieldsOfStudy"
)


def _get_ss_key() -> str:
    """Load Semantic Scholar API key from .env."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "SEMANTIC_SCHOLAR_API_KEY":
                    return v.strip()
    return os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")


def search_semantic_scholar(query: str, max_results: int = 5) -> List[Dict]:
    """Search Semantic Scholar by query, return list of article dicts.

    Excellent coverage of medical/computer science papers.
    Requires SEMANTIC_SCHOLAR_API_KEY in .env (free tier: 100 req/min).
    Without key: very limited rate (1 req/sec) — may miss results.
    """
    api_key = _get_ss_key()
    params = {
        "query": query,
        "limit": min(max_results, 10),
        "fields": PAPER_FIELDS,
    }
    url = f"{SEMANTIC_SCHOLAR_API}?{urllib.parse.urlencode(params)}"

    headers = {
        "User-Agent": "Hermy-Research/1.0 (mailto:hermy@local)",
        "Accept": "application/json",
    }
    if api_key:
        headers["x-api-key"] = api_key

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return [{"error": "Semantic Scholar rate limit reached (need API key)"}]
        return [{"error": f"Semantic Scholar search failed: {e.code}"}]
    except Exception as e:
        return [{"error": f"Semantic Scholar search failed: {e}"}]

    results = []
    for item in data.get("data", []):
        # DOI from externalIds
        external_ids = item.get("externalIds", {}) or {}
        doi = external_ids.get("DOI", "")

        # Open Access PDF
        oa_pdf = item.get("openAccessPdf", {}) or {}
        oa_pdf_url = oa_pdf.get("url", "") if isinstance(oa_pdf, dict) else ""

        # Authors
        authors_list = item.get("authors", []) or []
        authors = [a.get("name", "") for a in authors_list if a.get("name")]
        authors = authors[:5]

        # Year
        year = str(item.get("year", "")) or ""

        # Abstract
        abstract = item.get("abstract", "") or ""

        # Journal / venue
        journal = item.get("journal", "") or item.get("venue", "") or ""

        results.append({
            "source": "Semantic Scholar",
            "title": item.get("title", "Unknown") or "Unknown",
            "abstract": abstract,
            "date": year,
            "url": item.get("url", "") or "",
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "type": item.get("fieldsOfStudy", [""])[0] if item.get("fieldsOfStudy") else "",
            "oa_pdf_url": oa_pdf_url,
            "citation_count": item.get("citationCount", 0),
        })

        time.sleep(0.15)  # be polite

    return results


def get_by_doi(doi: str) -> Dict:
    """Fetch a single paper by DOI from Semantic Scholar."""
    encoded_doi = urllib.parse.quote(doi, safe="")
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{encoded_doi}?fields={PAPER_FIELDS}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Hermy-Research/1.0 (mailto:hermy@local)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"Semantic Scholar DOI lookup failed: {e}"}

    external_ids = data.get("externalIds", {}) or {}
    oa_pdf = data.get("openAccessPdf", {}) or {}

    return {
        "source": "Semantic Scholar",
        "title": data.get("title", "Unknown") or "Unknown",
        "abstract": data.get("abstract", "") or "",
        "date": str(data.get("year", "")) or "",
        "url": data.get("url", "") or "",
        "authors": [a.get("name", "") for a in data.get("authors", []) or [] if a.get("name")],
        "doi": external_ids.get("DOI", ""),
        "journal": data.get("journal", "") or data.get("venue", "") or "",
        "oa_pdf_url": oa_pdf.get("url", "") if isinstance(oa_pdf, dict) else "",
    }