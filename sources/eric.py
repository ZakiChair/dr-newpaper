"""ERIC source — Education Resources Information Center.
API docs: https://eric.ed.gov/?developer
Free API (requires key for higher rate limits, but basic access is free).
Covers 1.8M+ education-related research documents.

Note: ERIC also has a free XML API at https://eric.ed.gov/?合
but the RID-Q API is cleaner for programmatic access.
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

# ERIC's old but functional JSON API
ERIC_API = "https://api.ies.ed.gov/eric"
ERIC_SEARCH = f"{ERIC_API}/v2/search"


def search_eric(query: str, max_results: int = 5, api_key: str = "") -> List[Dict]:
    """Search ERIC by query, return list of article dicts.

    ERIC covers: journal articles, books, conference papers, theses,
    and technical reports in education research.

    NOTE: The free API endpoint (api.ies.ed.gov/eric/v2/search) returns 403
    Forbidden — an API key is required. Set ERIC_API_KEY in .env to enable.
    Without a key, this returns an error.
    """
    # Check for API key
    import os
    if not api_key:
        api_key = os.environ.get("ERIC_API_KEY", "")

    if not api_key:
        return [{"error": "ERIC API key required — set ERIC_API_KEY in .env (free at eric.ed.gov/developer)"}]

    params = {
        "q": query,
        "per_page": min(max_results, 100),
        "index": "TX",
        "token": api_key,
    }

    url = f"{ERIC_SEARCH}?" + urllib.parse.urlencode(params)

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
        return [{"error": f"ERIC search failed: {e}"}]

    items = data.get("response", {}).get("$values", []) or []
    results = []

    for item in items:
        if not isinstance(item, dict):
            continue

        # ERIC ID
        eric_id = item.get("id", "") or ""

        # DOI
        doi = item.get("doi", "") or ""

        # Title
        title = item.get("title", "") or "Unknown"

        # Authors — sometimes stored as list, sometimes as string
        authors_raw = item.get("authors", []) or item.get("author", []) or []
        if isinstance(authors_raw, str):
            authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
        elif isinstance(authors_raw, list):
            authors = [a for a in authors_raw if a][:5]
        else:
            authors = []

        # Date
        date = item.get("publicationDate", "") or item.get("year", "") or ""

        # Abstract
        abstract = item.get("abstract", "") or ""
        if not abstract:
            abstract = item.get("description", "") or ""

        # Journal / Source
        journal = item.get("journalName", "") or item.get("publicationTitle", "") or ""

        # Type — e.g. "Journal Articles", "Books", "Reports"
        doc_type = item.get("publicationType", "") or item.get("type", "") or ""

        # ERIC URL
        url_ref = f"https://eric.ed.gov/?id={eric_id}" if eric_id else ""

        # Full text available?
        full_text_url = item.get("fullTextUrl", "") or ""
        if isinstance(full_text_url, list):
            full_text_url = full_text_url[0] if full_text_url else ""
        is_oa = bool(full_text_url)

        results.append({
            "source": "ERIC",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "type": doc_type,
            "is_oa": is_oa,
            "full_text_url": full_text_url,
            "eric_id": eric_id,
        })

        time.sleep(0.15)

    return results


def get_by_eric_id(eric_id: str, api_key: str = "") -> Dict:
    """Fetch a single document by ERIC ID."""
    url = f"{ERIC_API}/v2/entries/{eric_id}"
    if api_key:
        url += f"?token={api_key}"

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
        return {"error": f"ERIC ID lookup failed: {e}"}

    item = data.get("response", {}) or data

    authors_raw = item.get("authors", []) or []
    if isinstance(authors_raw, str):
        authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
    elif isinstance(authors_raw, list):
        authors = [a for a in authors_raw if a][:5]
    else:
        authors = []

    full_text_url = item.get("fullTextUrl", "") or ""
    if isinstance(full_text_url, list):
        full_text_url = full_text_url[0] if full_text_url else ""

    return {
        "source": "ERIC",
        "title": item.get("title", "") or "Unknown",
        "abstract": item.get("abstract", "") or "",
        "date": item.get("publicationDate", "") or "",
        "url": f"https://eric.ed.gov/?id={eric_id}",
        "authors": authors,
        "doi": item.get("doi", "") or "",
        "journal": item.get("journalName", "") or "",
        "type": item.get("publicationType", "") or "",
        "is_oa": bool(full_text_url),
        "full_text_url": full_text_url,
        "eric_id": eric_id,
    }
