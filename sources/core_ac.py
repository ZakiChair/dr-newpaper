"""CORE.ac.uk source — aggregator of open access research papers.
API docs: https://api.core.ac.uk/docs
Rate limits: ~10 req/sec, no key required for basic usage.
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

CORE_API = "https://api.core.ac.uk/v3/search/works"


def search_core(query: str, max_results: int = 5) -> List[Dict]:
    """Search CORE by query, return list of article dicts.

    CORE aggregates open access papers from universities worldwide.
    Great for finding OA PDFs directly.
    """
    params = {
        "q": query,
        "limit": min(max_results, 20),  # CORE caps at 20 per page
        "offset": 0,
        "exclude": "fullText",
    }
    url = f"{CORE_API}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Hermy-Research/1.0 (mailto:hermy@local)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return [{"error": f"CORE search failed: {e}"}]

    results = []
    for item in data.get("results", []):
        # DOI
        dois = item.get("doi", [])
        doi = dois[0] if isinstance(dois, list) and dois else ""
        if not doi:
            doi = item.get("doi", "") or ""

        # Authors
        authors_raw = item.get("authors", []) or []
        if isinstance(authors_raw, list):
            authors = []
            for a in authors_raw:
                if isinstance(a, dict):
                    name = a.get("name", "")
                    if name:
                        authors.append(name)
                elif isinstance(a, str):
                    authors.append(a)
            authors = authors[:5]
        else:
            authors = []

        # Date
        date = item.get("publishedDate", "") or ""
        if not date:
            date = item.get("year", "") or ""

        # Abstract
        abstract = item.get("abstract", "") or ""
        if not abstract:
            abstract = item.get("description", "") or ""

        # Journal
        journal = item.get("journal", "") or item.get("venue", "") or ""

        # Direct PDF download URL (CORE has full-text download)
        download_url = item.get("downloadUrl", "") or item.get("fullTextUrl", "") or ""

        # Language
        lang = item.get("language", "") or ""
        if lang and isinstance(lang, list):
            lang = lang[0] if lang else ""

        results.append({
            "source": "CORE",
            "title": item.get("title", "Unknown") or "Unknown",
            "abstract": abstract,
            "date": date,
            "url": item.get("url", "") or item.get("coreUrl", "") or "",
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "type": item.get("type", "") or "",
            "oa_pdf_url": download_url,  # CORE direct download URL
            "lang": lang,
        })

        time.sleep(0.15)  # be polite

    return results


def get_by_doi(doi: str) -> Dict:
    """Fetch a single work by DOI from CORE."""
    encoded_doi = urllib.parse.quote(doi, safe="")
    url = f"https://api.core.ac.uk/v3/works/{encoded_doi}"

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
        return {"error": f"CORE DOI lookup failed: {e}"}

    authors_raw = data.get("authors", []) or []
    if isinstance(authors_raw, list):
        authors = []
        for a in authors_raw:
            if isinstance(a, dict):
                name = a.get("name", "")
                if name:
                    authors.append(name)
            elif isinstance(a, str):
                authors.append(a)
    else:
        authors = []

    return {
        "source": "CORE",
        "title": data.get("title", "Unknown") or "Unknown",
        "abstract": data.get("abstract", "") or data.get("description", "") or "",
        "date": data.get("publishedDate", "") or data.get("year", "") or "",
        "url": data.get("url", "") or data.get("coreUrl", "") or "",
        "authors": authors,
        "doi": doi,
        "journal": data.get("journal", "") or data.get("venue", "") or "",
        "oa_pdf_url": data.get("downloadUrl", "") or data.get("fullTextUrl", "") or "",
    }