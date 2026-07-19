"""Europe PMC source — 36M+ articles, books, patents, preprints.
API: https://www.ebi.ac.uk/europepmc/webservices/rest
Free, no key required. Excellent biomedical coverage.
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def search_europe_pmc(query: str, max_results: int = 5) -> List[Dict]:
    """Search Europe PMC by query, return list of article dicts.

    Europe PMC covers: PubMed abstracts + full-text OA content,
    books, patents, and preprints. Great for finding OA PDFs.
    Response format: data['resultList']['result'] — each item has
    title, doi, pmid, pubYear, authorString, journalInfo.journal.title,
    abstractText, fullTextUrlList, isOpenAccess.
    """
    params = {
        "query": query,
        "resulttype": "core",
        "pageSize": min(max_results, 100),
        "format": "json",
    }
    url = f"{EUROPE_PMC_BASE}/search?" + urllib.parse.urlencode(params)

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
        return [{"error": f"Europe PMC search failed: {e}"}]

    hits = data.get("resultList", {}).get("result", [])
    results = []

    for item in hits:
        # DOI
        doi = item.get("doi", "") or ""

        # PMID
        pmid = item.get("pmid", "") or ""

        # Title
        title = item.get("title", "") or "Unknown"

        # Authors — stored as semicolon string + structured list
        author_string = item.get("authorString", "") or ""
        author_list = item.get("authorList", {}).get("author", []) or []
        if isinstance(author_list, list) and author_list:
            authors = []
            for a in author_list:
                if isinstance(a, dict):
                    name = (a.get("firstName", "") + " " + a.get("lastName", "")).strip()
                    if name:
                        authors.append(name)
            authors = authors[:5] if authors else [a.strip() for a in author_string.split(";") if a.strip()][:5]
        else:
            authors = [a.strip() for a in author_string.split(";") if a.strip()][:5]

        # Date — prefer pubYear, then firstPublicationDate
        date = str(item.get("pubYear", "")) or item.get("firstPublicationDate", "") or ""

        # Abstract
        abstract = item.get("abstractText", "") or ""

        # Journal — nested: journalInfo.journal.title
        journal_info = item.get("journalInfo", {}) or {}
        journal_inner = journal_info.get("journal", {}) or {}
        journal = journal_inner.get("title", "") or journal_info.get("journalTitle", "") or ""

        # OA PDF URL — fullTextUrlList contains dicts with url/availability
        oa_pdf_url = ""
        ft_list = item.get("fullTextUrlList", {}).get("fullTextUrl", []) or []
        if isinstance(ft_list, list):
            for ft in ft_list:
                if isinstance(ft, dict) and ft.get("availability", "") in ("Open Access", "Open access"):
                    u = ft.get("url", "")
                    if u:
                        oa_pdf_url = u
                        break
                elif isinstance(ft, str):
                    if ft:
                        oa_pdf_url = ft
                        break

        # Is OA flag
        is_oa = item.get("isOpenAccess", "") in ("Y", "Open Access", True)

        # Pub type
        pub_types = item.get("pubTypeList", {}).get("pubType", []) or []
        if isinstance(pub_types, list):
            doc_type = pub_types[0] if pub_types else ""
        else:
            doc_type = ""

        # Source DB
        db_source = item.get("source", "") or "Europe PMC"

        # Direct URL
        if pmid:
            url_ref = f"https://europepmc.org/{pmid}"
        elif doi:
            url_ref = f"https://doi.org/{doi}"
        else:
            url_ref = ""

        results.append({
            "source": db_source,
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url_ref,
            "authors": authors,
            "doi": doi,
            "journal": journal,
            "pmid": pmid,
            "oa_pdf_url": oa_pdf_url,
            "type": doc_type,
            "is_oa": is_oa,
        })

        time.sleep(0.1)

    return results


def get_by_doi(doi: str) -> Dict:
    """Fetch a single article by DOI from Europe PMC."""
    encoded_doi = urllib.parse.quote(doi, safe="")
    params = {"format": "json"}
    url = f"{EUROPE_PMC_BASE}/match?doi={encoded_doi}&" + urllib.parse.urlencode(params)

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
        return {"error": f"Europe PMC DOI lookup failed: {e}"}

    # match result has 'result' which is a list
    match_result = data.get("result", [])
    if isinstance(match_result, list) and match_result:
        raw = match_result[0]
        # Get the core data via details endpoint
        pmid = raw.get("pmid", "") or ""
        return get_by_pmid(pmid) if pmid else {"error": "No PMID found"}
    return {"error": "DOI not found in Europe PMC"}


def get_by_pmid(pmid: str) -> Dict:
    """Fetch a single article by PMID from Europe PMC."""
    params = {"format": "json"}
    url = f"{EUROPE_PMC_BASE}/fetch?pmcid={pmid}&" + urllib.parse.urlencode(params)

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
    except Exception:
        # Fallback to search by pmid
        url = f"{EUROPE_PMC_BASE}/search?query=PMID:{pmid}&pageSize=1&format=json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Hermy-Research/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"Europe PMC PMID lookup failed: {e}"}

    hits = data.get("resultList", {}).get("result", [])
    if not hits:
        return {"error": "PMID not found in Europe PMC"}
    item = hits[0]

    doi = item.get("doi", "") or ""
    author_string = item.get("authorString", "") or ""
    author_list = item.get("authorList", {}).get("author", []) or []
    if isinstance(author_list, list) and author_list:
        authors = []
        for a in author_list:
            if isinstance(a, dict):
                name = (a.get("firstName", "") + " " + a.get("lastName", "")).strip()
                if name:
                    authors.append(name)
        authors = authors[:5] if authors else [a.strip() for a in author_string.split(";") if a.strip()][:5]
    else:
        authors = [a.strip() for a in author_string.split(";") if a.strip()][:5]

    journal_info = item.get("journalInfo", {}) or {}
    journal_inner = journal_info.get("journal", {}) or {}
    journal = journal_inner.get("title", "") or ""

    oa_pdf_url = ""
    ft_list = item.get("fullTextUrlList", {}).get("fullTextUrl", []) or []
    if isinstance(ft_list, list):
        for ft in ft_list:
            if isinstance(ft, dict) and ft.get("availability", "") in ("Open Access", "Open access"):
                u = ft.get("url", "")
                if u:
                    oa_pdf_url = u
                    break

    return {
        "source": "Europe PMC",
        "title": item.get("title", "") or "Unknown",
        "abstract": item.get("abstractText", "") or "",
        "date": str(item.get("pubYear", "")) or "",
        "url": f"https://europepmc.org/{item.get('pmid', '')}",
        "authors": authors,
        "doi": doi,
        "journal": journal,
        "pmid": item.get("pmid", "") or "",
        "oa_pdf_url": oa_pdf_url,
    }
