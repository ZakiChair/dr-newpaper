"""OpenAlex source — free API from the Open Citations project.
API docs: https://api.openalex.org
Rate limit: ~10 req/sec, no key needed.
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

OPENALEX_API = "https://api.openalex.org/works"


def search_openalex(query: str, max_results: int = 5) -> List[Dict]:
    """Search OpenAlex by query, return list of article dicts.

    OpenAlex is an open catalog of scholarly works — great coverage,
    includes OA (open access) PDF links when available.
    """
    params = {
        "search": query,
        "per-page": max_results,
    }
    url = f"{OPENALEX_API}?{urllib.parse.urlencode(params)}"

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
        return [{"error": f"OpenAlex search failed: {e}"}]

    results = []
    for item in data.get("results", []):

        # DOI
        doi = ""
        for id_ in item.get("ids", {}).values():
            if isinstance(id_, str) and id_.startswith("10."):
                doi = id_
                break
        if not doi:
            doi = item.get("doi", "")

        # Title
        title = item.get("title", "Unknown")

        # Authors
        authors = []
        for aut in item.get("authorships", []):
            author = aut.get("author", {})
            name = author.get("display_name", "")
            if name:
                authors.append(name)
        authors = authors[:5]

        # Date
        date = item.get("publication_date", "")
        if not date:
            date = item.get("created_date", "")

        # Abstract (Inverted Index — needs reconstruction)
        abstract = _reconstruct_abstract(item)

        # Journal
        journal = ""
        primary_location = item.get("primary_location", {})
        if primary_location:
            source_obj = primary_location.get("source")
            if source_obj:
                journal = source_obj.get("display_name", "") or ""

        # Open Access PDF link
        oa_pdf_url = ""
        for loc in item.get("locations", []):
            pdf_url = loc.get("pdf_url")
            if pdf_url:
                oa_pdf_url = pdf_url
                break

        # URL
        url_ref = item.get("doi", "")
        if not url_ref:
            url_ref = item.get("id", "")

        # Type
        article_type = item.get("type", "")

        results.append({
            "source": "OpenAlex",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "type": article_type,
            "oa_pdf_url": oa_pdf_url,  # direct OA PDF if available
        })

        time.sleep(0.1)  # be polite

    return results


def _reconstruct_abstract(item: dict) -> str:
    """OpenAlex stores abstract_inverted_index as word → {positions}.
    Reconstruct the original abstract text.
    """
    inv_index = item.get("abstract_inverted_index", {})
    if not inv_index:
        return ""

    try:
        # Build a list of (position, word)
        word_positions = []
        for word, positions in inv_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        # Sort by position and join
        word_positions.sort(key=lambda x: x[0])
        return " ".join(word for _, word in word_positions)
    except Exception:
        return ""


def get_by_doi(doi: str) -> Dict:
    """Fetch a single work by DOI from OpenAlex."""
    # OpenAlex DOI format: https://doi.org/10.xxxx/xxxx
    # API accepts the full URL with https://doi.org/ prefix
    encoded_doi = urllib.parse.quote(doi, safe="")
    if not encoded_doi.startswith("https"):
        encoded_doi = urllib.parse.quote(f"https://doi.org/{doi}", safe="")
    url = f"{OPENALEX_API}/{encoded_doi}"

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
        return {"error": f"OpenAlex DOI lookup failed: {e}"}

    item = data

    doi = ""
    for id_ in item.get("ids", {}).values():
        if isinstance(id_, str) and id_.startswith("10."):
            doi = id_
            break
    if not doi:
        doi = item.get("doi", "")

    title = item.get("title", "Unknown")

    authors = []
    for aut in item.get("authorships", []):
        author = aut.get("author", {})
        name = author.get("display_name", "")
        if name:
            authors.append(name)

    date = item.get("publication_date", "")
    abstract = _reconstruct_abstract(item)

    journal = ""
    primary_location = item.get("primary_location", {})
    if primary_location:
        source_obj = primary_location.get("source")
        journal = source_obj.get("display_name", "") or "" if source_obj else ""

    oa_pdf_url = ""
    for loc in item.get("locations", []):
        pdf_url = loc.get("pdf_url")
        if pdf_url:
            oa_pdf_url = pdf_url
            break

    return {
        "source": "OpenAlex",
        "title": title,
        "abstract": abstract,
        "date": date,
        "url": item.get("doi", ""),
        "authors": authors,
        "doi": doi,
        "journal": journal,
        "oa_pdf_url": oa_pdf_url,
    }
