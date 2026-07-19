"""CrossRef source — free API, 50k req/day, returns DOI + full metadata."""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

CROSSREF_API = "https://api.crossref.org/works"


def search_crossref(query: str, max_results: int = 5) -> List[Dict]:
    """Search CrossRef by query, return list of article dicts.

    CrossRef is a DOI registry — great for getting DOIs that can then
    be used with Sci-Hub for PDF retrieval.
    """
    params = {
        "query": query,
        "rows": max_results,
        "sort": "relevance",
    }
    url = f"{CROSSREF_API}?{urllib.parse.urlencode(params)}"

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
        return [{"error": f"CrossRef search failed: {e}"}]

    items = data.get("message", {}).get("items", [])
    results = []

    for item in items:
        # DOI
        doi = item.get("DOI", "")

        # Title — is a list
        titles = item.get("title", [])
        title = titles[0] if titles else "Unknown"

        # Authors
        authors = []
        for aut in item.get("author", []):
            name = " ".join(filter(None, [aut.get("given", ""), aut.get("family", "")]))
            if name:
                authors.append(name)
        authors = authors[:5]

        # Date — prefer published-print or published-online
        date = ""
        for date_field in ["published-print", "published-online", "created"]:
            d = item.get(date_field, {}).get("date-parts", [])
            if d and d[0]:
                parts = d[0]
                date = "-".join(str(p).zfill(2) for p in parts)
                break

        # Abstract (not always present)
        abstract = item.get("abstract", "") or ""
        # Strip HTML tags from abstract
        import re as _re
        abstract = _re.sub(r"<[^>]+>", "", abstract)

        # Journal / container
        container = ""
        if item.get("container-title"):
            container = item["container-title"][0]

        # URL to the article
        url_ref = item.get("URL", f"https://doi.org/{doi}" if doi else "#")

        # Type
        article_type = item.get("type", "journal-article")

        results.append({
            "source": "CrossRef",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": doi,
            "journal": container,
            "type": article_type,
        })

        time.sleep(0.1)  # be polite to CrossRef API

    return results


def get_by_doi(doi: str) -> Dict:
    """Fetch a single article by DOI from CrossRef."""
    url = f"{CROSSREF_API}/{urllib.parse.quote(doi)}"
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
        return {"error": f"CrossRef DOI lookup failed: {e}"}

    item = data.get("message", {})
    doi = item.get("DOI", doi)
    titles = item.get("title", [])
    title = titles[0] if titles else "Unknown"

    authors = []
    for aut in item.get("author", []):
        name = " ".join(filter(None, [aut.get("given", ""), aut.get("family", "")]))
        if name:
            authors.append(name)

    date = ""
    for date_field in ["published-print", "published-online", "created"]:
        d = item.get(date_field, {}).get("date-parts", [])
        if d and d[0]:
            parts = d[0]
            date = "-".join(str(p).zfill(2) for p in parts)
            break

    abstract = item.get("abstract", "") or ""
    import re as _re
    abstract = _re.sub(r"<[^>]+>", "", abstract)

    container = ""
    if item.get("container-title"):
        container = item["container-title"][0]

    return {
        "source": "CrossRef",
        "title": title,
        "abstract": abstract,
        "date": date,
        "url": item.get("URL", f"https://doi.org/{doi}"),
        "authors": authors,
        "doi": doi,
        "journal": container,
    }
