"""BASE (Bielefeld Academic Search Engine) source — 300M+ items.
API docs: https://base.uni-bielefeld.de/docs
Free, no key required. Excellent for multidisciplinary academic search.
Covers institutional repositories, journals, and databases worldwide.
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

BASE_API = "https://api.base.uni-bielefeld.de"
BASE_SEARCH = f"{BASE_API}/search"


def search_base(query: str, max_results: int = 5) -> List[Dict]:
    """Search BASE by query, return list of article dicts.

    BASE covers: institutional repositories, journals, data sets,
    and many OA sources. Great for finding OA documents.
    """
    params = {
        "q": query,
        "f": "doc Type:article",        # filter: articles only
        "rows": min(max_results, 50),
        "output": "json",
    }
    url = f"{BASE_SEARCH}?" + urllib.parse.urlencode(params)

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
        return [{"error": f"BASE search failed: {e}"}]

    # BASE returns a large JSON; results are under 'response.docs'
    docs = data.get("response", {}).get("docs", []) or []
    results = []

    for item in docs:
        if not isinstance(item, dict):
            continue

        # DOI
        doi_list = item.get("doi", [])
        doi = doi_list[0] if isinstance(doi_list, list) and doi_list else ""

        # Title — list of strings, take first
        titles = item.get("title", []) or []
        title = titles[0] if isinstance(titles, list) and titles else str(titles) if titles else "Unknown"
        # Clean HTML from title
        import re as _re
        title = _re.sub(r"<[^>]+>", "", title)

        # Authors — list of strings
        authors_raw = item.get("authors", []) or []
        if isinstance(authors_raw, list):
            authors = [a for a in authors_raw if a][:5]
        else:
            authors = [str(authors_raw)]

        # Date
        dates = item.get("publishDateSort", []) or item.get("publishDate", []) or []
        if isinstance(dates, list):
            date = dates[0] if dates else ""
        else:
            date = str(dates)

        # Abstract / description
        descriptions = item.get("description", []) or []
        if isinstance(descriptions, list):
            abstract = descriptions[0] if descriptions else ""
        else:
            abstract = str(descriptions)
        abstract = _re.sub(r"<[^>]+>", "", abstract)

        # Journal / source
        sources = item.get("sourceTitle", []) or item.get("journal", []) or []
        if isinstance(sources, list):
            journal = sources[0] if sources else ""
        else:
            journal = str(sources)

        # Direct URL
        urls = item.get("url", []) or item.get("url", "") or ""
        if isinstance(urls, list):
            url_ref = urls[0] if urls else ""
        else:
            url_ref = str(urls)

        # Type / format
        types = item.get("type", []) or []
        if isinstance(types, list):
            doc_type = types[0] if types else ""
        else:
            doc_type = str(types)

        # OA status
        oa_statuses = item.get("access，免费", []) or []  # BASE uses Chinese chars for OA
        oa_note = item.get("access", []) or []
        is_oa = "free" in str(oa_note).lower() or "oa" in str(oa_note).lower()

        results.append({
            "source": "BASE",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "type": doc_type,
            "is_oa": is_oa,
        })

        time.sleep(0.1)

    return results


def search_base_all(query: str, max_results: int = 5) -> List[Dict]:
    """Search BASE without article-type filter (all document types)."""
    params = {
        "q": query,
        "rows": min(max_results, 50),
        "output": "json",
    }
    url = f"{BASE_SEARCH}?" + urllib.parse.urlencode(params)

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
        return [{"error": f"BASE search failed: {e}"}]

    docs = data.get("response", {}).get("docs", []) or []
    results = []

    for item in docs:
        if not isinstance(item, dict):
            continue

        doi_list = item.get("doi", [])
        doi = doi_list[0] if isinstance(doi_list, list) and doi_list else ""
        titles = item.get("title", []) or []
        title = titles[0] if isinstance(titles, list) and titles else str(titles) if titles else "Unknown"
        import re as _re
        title = _re.sub(r"<[^>]+>", "", title)
        authors_raw = item.get("authors", []) or []
        if isinstance(authors_raw, list):
            authors = [a for a in authors_raw if a][:5]
        else:
            authors = [str(authors_raw)]
        dates = item.get("publishDateSort", []) or item.get("publishDate", []) or []
        date = dates[0] if isinstance(dates, list) else str(dates)
        descriptions = item.get("description", []) or []
        abstract = descriptions[0] if isinstance(descriptions, list) and descriptions else str(descriptions) if descriptions else ""
        abstract = _re.sub(r"<[^>]+>", "", abstract)
        sources = item.get("sourceTitle", []) or []
        journal = sources[0] if isinstance(sources, list) and sources else ""
        urls = item.get("url", []) or ""
        url_ref = urls[0] if isinstance(urls, list) and urls else str(urls)
        types = item.get("type", []) or []
        doc_type = types[0] if isinstance(types, list) and types else ""

        results.append({
            "source": "BASE",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "type": doc_type,
        })

        time.sleep(0.1)

    return results
