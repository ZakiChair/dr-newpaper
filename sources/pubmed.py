"""PubMed source — uses NCBI E-utilities API."""

import re
import urllib.request
import urllib.parse
from typing import List, Dict

PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def search_pubmed(query: str, max_results: int = 5, email: str = "hermy@local") -> List[Dict]:
    """Search PubMed by query, return list of article dicts with abstracts."""
    # Step 1: search for IDs
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "sort": "relevance",  # Most relevant papers first
        "email": email,
    }
    url = f"{PUBMED_SEARCH}?{urllib.parse.urlencode(search_params)}"

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            search_data = _json_loads(resp.read())
    except Exception as e:
        return [{"error": f"PubMed search failed: {e}"}]

    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    # Step 2: fetch full records via efetch (XML)
    ids_str = ",".join(ids)
    fetch_url = f"{PUBMED_FETCH}?db=pubmed&id={ids_str}&rettype=abstract&retmode=xml&email={email}"

    try:
        with urllib.request.urlopen(fetch_url, timeout=20) as resp:
            xml_data = resp.read()
    except Exception as e:
        return [{"error": f"PubMed fetch failed: {e}"}]

    return _parse_pubmed_xml(xml_data)


def _json_loads(data: bytes) -> dict:
    import json
    return json.loads(data.decode("utf-8"))


def _parse_pubmed_xml(xml_data: bytes) -> List[Dict]:
    """Parse PubMed XML to extract articles with abstracts."""
    results = []
    try:
        from xml.etree import ElementTree as ET
    except ImportError:
        return [{"error": "xml.etree not available"}]

    try:
        root = ET.fromstring(xml_data)
    except Exception as e:
        return [{"error": f"XML parse failed: {e}"}]

    ns = {"pm": "https://www.ncbi.nlm.nih.gov/pmc/articles"}

    for article in root.findall("PubmedArticle"):
        pmid_el = article.find(".//PMID")
        pmid = pmid_el.text if pmid_el is not None else ""

        title_el = article.find("MedlineCitation/Article/ArticleTitle")
        title = title_el.text if title_el is not None else "Unknown"

        # Extract abstract — can be in one or multiple AbstractText elements
        abstract_parts = []
        for abs_el in article.findall(".//AbstractText"):
            label = abs_el.get("Label", "")
            text = abs_el.text or ""
            if text.strip():
                if label:
                    abstract_parts.append(f"{label}: {text.strip()}")
                else:
                    abstract_parts.append(text.strip())
        abstract = " ".join(abstract_parts)

        # Date
        date_parts = []
        for date_el in article.findall(".//ArticleDate"):
            for child in ["Year", "Month", "Day"]:
                d = date_el.find(child)
                if d is not None and d.text:
                    date_parts.append(d.text)
        date = "-".join(date_parts) if date_parts else ""

        if not date:
            # Try PubDate as fallback
            pub_date = article.find(".//PubDate")
            if pub_date is not None:
                parts = []
                for child in ["Year", "Month", "Day"]:
                    d = pub_date.find(child)
                    if d is not None and d.text:
                        parts.append(d.text)
                date = "-".join(parts)

        # Authors
        authors = []
        for auth_el in article.findall(".//Author"):
            last_name = auth_el.find("LastName")
            fore_name = auth_el.find("ForeName")
            if last_name is not None and last_name.text:
                name = last_name.text
                if fore_name is not None and fore_name.text:
                    name = f"{fore_name.text} {name}"
                authors.append(name)
        authors = authors[:5]

        # DOI
        doi = ""
        for el in article.findall(".//ArticleId[@IdType='doi']"):
            if el.text:
                doi = el.text
                break

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "#"

        results.append({
            "source": "PubMed",
            "title": title,
            "abstract": abstract,
            "date": date,
            "url": url,
            "authors": authors,
            "doi": doi,
        })

    return results
