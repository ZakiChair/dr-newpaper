"""bioRxiv / medRxiv source — preprints from Cold Spring Harbor Laboratory.
API docs:
  - OpenAlex: https://api.openalex.org (has type:preprint filter, covers bioRxiv + medRxiv)
  - bioRxiv details: https://www.biorxiv.org/about-biorxiv/api (only DOI lookup, no search)

Search strategy:
- Primary: OpenAlex with filter=type:preprint — returns bioRxiv and medRxiv preprints
  with proper abstracts (via abstract_inverted_index).
- Fallback: if OpenAlex fails, return empty list (don't loop back to Europe PMC — Europe PMC
  doesn't reliably return bioRxiv/medRxiv preprints via DOI filtering).

Enrichment: get_by_doi() uses the bioRxiv /details/{server}/{doi} endpoint to get
the latest version data (abstract, authors, category) from the preprint server itself.
"""

import urllib.request
import urllib.parse
import json
import re
import time
from typing import List, Dict

OPENALEX_API = "https://api.openalex.org/works"
BIOARXIV_DETAILS = "https://api.biorxiv.org/details/biorxiv"
MEDARXIV_DETAILS = "https://api.biorxiv.org/details/medrxiv"


def _reconstruct_abstract(inv_index: dict) -> str:
    """Reconstruct abstract from OpenAlex inverted index."""
    if not inv_index:
        return ""
    try:
        word_positions = []
        for word, positions in inv_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort(key=lambda x: x[0])
        return " ".join(word for _, word in word_positions)
    except Exception:
        return ""


def _parse_openalex_preprint(item: Dict, source: str) -> Dict:
    """Parse an OpenAlex preprint item into our article format."""
    # DOI
    doi = ""
    for id_ in item.get("ids", {}).values():
        if isinstance(id_, str) and id_.startswith("10."):
            doi = id_
            break
    if not doi:
        doi = item.get("doi", "") or ""

    # Title
    title = item.get("title", "Unknown") or "Unknown"

    # Authors
    authors = []
    for aut in item.get("authorships", []):
        author = aut.get("author", {})
        name = author.get("display_name", "")
        if name:
            authors.append(name)
    authors = authors[:5]

    # Date
    date = item.get("publication_date", "") or ""

    # Abstract (Inverted Index)
    abstract = _reconstruct_abstract(item.get("abstract_inverted_index", {}))

    # Journal / source name
    primary_location = item.get("primary_location") or {}
    source_obj = (primary_location.get("source") or {}) if primary_location else {}
    journal = source_obj.get("display_name", "") or source

    # OA PDF URL
    oa_pdf_url = ""
    for loc in item.get("locations", []):
        pdf = loc.get("pdf_url")
        if pdf:
            oa_pdf_url = pdf
            break

    return {
        "source": source,
        "title": title,
        "abstract": abstract,
        "date": date,
        "url": item.get("doi", "") or f"https://doi.org/{doi}" if doi else "",
        "authors": authors,
        "doi": doi,
        "journal": journal,
        "type": "preprint",
        "is_preprint": True,
        "oa_pdf_url": oa_pdf_url,
    }


def search_biorxiv(query: str, max_results: int = 5) -> List[Dict]:
    """Search bioRxiv preprints via OpenAlex type:preprint filter.

    OpenAlex indexes bioRxiv preprints and supports filtering by type=preprint
    and source=bioRxiv. This is the most reliable way to get bioRxiv search results.
    """
    params = {
        "search": query,
        "filter": "type:preprint",
        "per-page": min(max_results, 20),
        "mailto": "hermy@local",
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
        return [{"error": f"bioRxiv (OpenAlex) search failed: {e}"}]

    results = data.get("results", [])
    biorxiv_results = []

    for item in results:
        primary_location = item.get("primary_location") or {}
        source_obj = (primary_location.get("source") or {}) if primary_location else {}
        source_display = source_obj.get("display_name", "") or ""

        # Filter: bioRxiv source
        if "bioRxiv" in source_display or "biorxiv" in source_display.lower():
            parsed = _parse_openalex_preprint(item, "bioRxiv")
            biorxiv_results.append(parsed)

    return biorxiv_results


def search_medrxiv(query: str, max_results: int = 5) -> List[Dict]:
    """Search medRxiv preprints via OpenAlex type:preprint filter.

    OpenAlex indexes medRxiv preprints and supports filtering by type=preprint.
    """
    params = {
        "search": query,
        "filter": "type:preprint",
        "per-page": min(max_results, 20),
        "mailto": "hermy@local",
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
        return [{"error": f"medRxiv (OpenAlex) search failed: {e}"}]

    results = data.get("results", [])
    medrxiv_results = []

    for item in results:
        primary_location = item.get("primary_location") or {}
        source_obj = (primary_location.get("source") or {}) if primary_location else {}
        source_display = source_obj.get("display_name", "") or ""

        # Filter: medRxiv source
        if "medRxiv" in source_display or "medrxiv" in source_display.lower():
            parsed = _parse_openalex_preprint(item, "medRxiv")
            medrxiv_results.append(parsed)

    return medrxiv_results


def get_by_doi(doi: str) -> Dict:
    """Fetch a single preprint by DOI from bioRxiv or medRxiv.

    Uses the /details/{server}/{doi} endpoint which returns versioned
    JSON lines — we take the latest version (last line).
    """
    encoded_doi = urllib.parse.quote(doi, safe="")
    is_medrxiv = "medrxiv" in doi.lower() or "10.20211" in doi
    base_url = MEDARXIV_DETAILS if is_medrxiv else BIOARXIV_DETAILS
    source = "medRxiv" if is_medrxiv else "bioRxiv"

    url = f"{base_url}/{encoded_doi}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Hermy-Research/1.0 (mailto:hermy@local)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            lines = [l for l in raw.strip().split("\n") if l.strip()]
            if not lines:
                return {"error": "Empty bio/medRxiv response"}
            # Last line = latest version
            data = json.loads(lines[-1])
    except Exception as e:
        return {"error": f"bio/medRxiv DOI lookup failed: {e}"}

    collection = data
    if isinstance(data, dict):
        collection = data.get("collection", data)
    if isinstance(collection, list):
        collection = collection[-1] if collection else {}
    if not isinstance(collection, dict):
        return {"error": "Malformed bio/medRxiv response"}

    doi = collection.get("doi", "") or doi
    title_raw = collection.get("title", "") or "Unknown"
    title = re.sub(r"<[^>]+>", "", title_raw).strip()

    authors_str = collection.get("authors", "") or ""
    authors = [a.strip() for a in authors_str.split(";") if a.strip()][:5]

    date = collection.get("date", "") or collection.get("rel_date", "") or ""
    abstract_raw = collection.get("abstract", "") or ""
    abstract = re.sub(r"<[^>]+>", "", abstract_raw).strip()

    category = collection.get("category", "") or ""

    oa_pdf_url = ""
    if doi and source == "bioRxiv":
        oa_pdf_url = f"https://www.biorxiv.org/content/{doi}.full.pdf"
    elif doi and source == "medRxiv":
        oa_pdf_url = f"https://www.medrxiv.org/content/{doi}.full.pdf"

    return {
        "source": source,
        "title": title,
        "abstract": abstract,
        "date": date,
        "url": f"https://doi.org/{doi}" if doi else "",
        "authors": authors,
        "doi": doi,
        "category": category,
        "type": "preprint",
        "is_preprint": True,
        "oa_pdf_url": oa_pdf_url,
    }