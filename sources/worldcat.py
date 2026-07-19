"""WorldCat source — world-wide library catalog (books, dissertations, articles).
API: https://worldcat.org/affiliate/tools/api/affiliate.search
Free access via OCLC's APIs. Great for: books, dissertations, conference proceedings.
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

WORLDCAT_SEARCH = "https://worldcat.org/affiliate/tools/api/affiliate.search"
WORLDCAT_BIBS = "https://worldcat.org/bib"


def search_worldcat(query: str, max_results: int = 5) -> List[Dict]:
    """Search WorldCat by query, return list of items.

    WorldCat covers: books, dissertations, conference proceedings,
    articles from 10,000+ libraries worldwide.
    """
    params = {
        "q": query,
        "servicelevel": "full",
        "rows": min(max_results, 20),
        "format": "json",
    }
    url = f"{WORLDCAT_SEARCH}?" + urllib.parse.urlencode(params)

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
        return [{"error": f"WorldCat search failed: {e}"}]

    results = []
    entries = data if isinstance(data, list) else data.get("results", []) or data.get("documents", []) or []

    for item in entries:
        if not isinstance(item, dict):
            continue

        # Title
        title = item.get("title", "") or "Unknown"

        # Authors
        authors_raw = item.get("author", []) or item.get("authors", []) or []
        if isinstance(authors_raw, str):
            authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
        elif isinstance(authors_raw, list):
            authors = []
            for a in authors_raw:
                if isinstance(a, dict):
                    name = a.get("name", "") or a.get("display", "") or ""
                    if name:
                        authors.append(name)
                elif isinstance(a, str):
                    authors.append(a)
        else:
            authors = []
        authors = authors[:5]

        # Date
        date = item.get("date", "") or item.get("year", "") or ""

        # Abstract / description
        abstract = item.get("abstract", "") or item.get("description", "") or ""

        # Type
        doc_type = item.get("type", "") or item.get("format", "") or ""

        # ISSN / ISBN
        isbn = item.get("isbn", "") or ""
        issn = item.get("issn", "") or ""

        # URL to the record
        url_ref = item.get("url", "") or item.get("recordURL", "") or ""

        # Publisher
        publisher = item.get("publisher", "") or ""

        results.append({
            "source": "WorldCat",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": "",  # WorldCat is primarily books/dissertations
            "isbn": isbn,
            "issn": issn,
            "journal": publisher,
            "type": doc_type,
        })

        time.sleep(0.15)

    return results


def search_worldcat_books(query: str, max_results: int = 5) -> List[Dict]:
    """Search WorldCat limiting to books only."""
    params = {
        "q": query,
        "itemType": "books",
        "servicelevel": "full",
        "rows": min(max_results, 20),
        "format": "json",
    }
    url = f"{WORLDCAT_SEARCH}?" + urllib.parse.urlencode(params)

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
        return [{"error": f"WorldCat books search failed: {e}"}]

    results = []
    entries = data if isinstance(data, list) else data.get("results", []) or []

    for item in entries:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "") or "Unknown"
        authors_raw = item.get("author", []) or []
        if isinstance(authors_raw, str):
            authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
        elif isinstance(authors_raw, list):
            authors = [a.get("name", "") or str(a) for a in authors_raw if a][:5]
        else:
            authors = []
        isbn = item.get("isbn", "") or ""
        date = item.get("date", "") or ""
        url_ref = item.get("url", "") or ""
        publisher = item.get("publisher", "") or ""

        results.append({
            "source": "WorldCat (Books)",
            "title": title,
            "abstract": "",
            "date": date,
            "url": url_ref,
            "authors": authors[:5],
            "doi": "",
            "isbn": isbn,
            "journal": publisher,
            "type": "book",
        })
        time.sleep(0.15)

    return results
