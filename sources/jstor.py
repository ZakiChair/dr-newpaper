"""JSTOR source — archives of historical academic journals.
API docs: https://www.jstor.org/api
IMPORTANT: JSTOR API requires API key (free for non-commercial use).
For deep research, JSTOR is primarily useful for historical/long-tail content.
Most content is paywalled — this source is best-effort OA only.
"""

import urllib.request
import urllib.parse
import json
import time
import os
from pathlib import Path
from typing import List, Dict

JSTOR_API = "https://api.jstor.org"
JSTOR_SEARCH = f"{JSTOR_API}/api/works"


def _get_jstor_key() -> str:
    """Load JSTOR API key from .env."""
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "JSTOR_API_KEY":
                    return v.strip()
    return os.getenv("JSTOR_API_KEY", "")


def search_jstor(query: str, max_results: int = 5) -> List[Dict]:
    """Search JSTOR by query, return list of article dicts.

    JSTOR requires API key (free registration at jstor.org).
    Without key, this returns an error.
    Most JSTOR content is paywalled — OA articles are limited.
    Best for: historical research, long-tail academic content.
    """
    api_key = _get_jstor_key()
    if not api_key:
        return [{"error": "JSTOR API key not configured — set JSTOR_API_KEY in .env"}]

    params = {
        "query": query,
        "items": min(max_results, 25),
        "info": "detail",
        "lang": "en",
    }
    url = f"{JSTOR_SEARCH}?" + urllib.parse.urlencode(params)

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Hermy-Research/1.0 (mailto:hermy@local)",
                "Accept": "application/json",
                "Authorization": f"Token {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return [{"error": "JSTOR API key invalid or unauthorized"}]
        return [{"error": f"JSTOR HTTP error {e.code}"}]
    except Exception as e:
        return [{"error": f"JSTOR search failed: {e}"}]

    items = data.get("response", {}).get("items", []) or []
    results = []

    for item in items:
        if not isinstance(item, dict):
            continue

        # JSTOR ID
        jstor_id = item.get("id", "") or ""

        # DOI (not always present)
        doi = item.get("doi", "") or ""

        # Title
        title = item.get("title", "") or "Unknown"

        # Authors
        authors_raw = item.get("creators", []) or item.get("author", []) or []
        if isinstance(authors_raw, list):
            authors = []
            for a in authors_raw:
                if isinstance(a, dict):
                    name = a.get("name", "") or a.get("creator", "") or ""
                    if name:
                        authors.append(name)
                elif isinstance(a, str):
                    authors.append(a)
            authors = authors[:5]
        else:
            authors = []

        # Date
        date_info = item.get("publicationDates", []) or item.get("publicationDate", {})
        if isinstance(date_info, list) and date_info:
            date = str(date_info[0])
        elif isinstance(date_info, dict):
            date = date_info.get("date", "") or ""
        else:
            date = ""

        # Abstract
        abstract = item.get("abstract", "") or ""
        if not abstract:
            abstract = item.get("description", "") or ""

        # Journal
        journal = item.get("containerTitle", "") or item.get("publicationTitle", "") or ""

        # Type
        doc_type = item.get("materialType", "") or item.get("type", "") or ""

        # URL
        url_ref = f"https://www.jstor.org/stable/{jstor_id}" if jstor_id else ""

        # Access — is it OA?
        access_status = item.get("accessStatus", {}) or {}
        if isinstance(access_status, dict):
            is_oa = access_status.get("isOpenAccess", False)
        else:
            is_oa = False

        results.append({
            "source": "JSTOR",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "type": doc_type,
            "is_oa": is_oa,
            "jstor_id": jstor_id,
        })

        time.sleep(0.2)

    return results


def get_by_doi(doi: str) -> Dict:
    """Fetch a single item by DOI from JSTOR."""
    api_key = _get_jstor_key()
    if not api_key:
        return {"error": "JSTOR API key not configured"}

    encoded_doi = urllib.parse.quote(doi, safe="")
    url = f"{JSTOR_API}/api/works/{encoded_doi}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Hermy-Research/1.0 (mailto:hermy@local)",
                "Accept": "application/json",
                "Authorization": f"Token {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"JSTOR DOI lookup failed: {e}"}

    item = data.get("response", {}).get("items", [{}])[0] or data

    authors_raw = item.get("creators", []) or []
    if isinstance(authors_raw, list):
        authors = []
        for a in authors_raw:
            if isinstance(a, dict):
                name = a.get("name", "") or ""
                if name:
                    authors.append(name)
        authors = authors[:5]
    else:
        authors = []

    return {
        "source": "JSTOR",
        "title": item.get("title", "") or "Unknown",
        "abstract": item.get("abstract", "") or "",
        "date": item.get("publicationDates", [{}])[0].get("date", "") if item.get("publicationDates") else "",
        "url": item.get("url", "") or f"https://www.jstor.org/stable/{item.get('id', '')}",
        "authors": authors,
        "doi": doi,
        "journal": item.get("containerTitle", "") or "",
        "type": item.get("materialType", "") or "",
    }
