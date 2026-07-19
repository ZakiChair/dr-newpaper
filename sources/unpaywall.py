"""Unpaywall source — find free OA PDF URLs by DOI.
API docs: https://unpaywall.org/api
Free, no key required. Returns OA status + direct PDF URL.
Complements OpenAlex/CrossRef by adding free PDF links.
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

UNPAYWALL_API = "https://api.unpaywall.org/v2"


def _get_email() -> str:
    """Return a polite email for Unpaywall API (required by their ToS)."""
    return "hermy@local"


def get_oa_pdf_url(doi: str) -> Dict:
    """Look up a single DOI for free OA PDF URL.

    Returns dict with:
      - oa (bool): is it OA?
      - best_url (str): direct URL to PDF (or landing page)
      - oa_status (str): "gold", "green", "bronze", "hybrid", "closed"
      - license (str): license name if known
      - version (str): "publishedVersion", "acceptedVersion", "submittedVersion"
    """
    email = _get_email()
    encoded_doi = urllib.parse.quote(doi, safe="")
    url = f"{UNPAYWALL_API}/{encoded_doi}?email={email}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Hermy-Research/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": "DOI not found in Unpaywall", "oa": False}
        return {"error": f"Unpaywall HTTP error {e.code}", "oa": False}
    except Exception as e:
        return {"error": f"Unpaywall lookup failed: {e}", "oa": False}

    is_oa = data.get("is_oa", False)
    best_url = ""
    oa_status = data.get("oa_status", "unknown") or "unknown"
    license_ = data.get("license", "") or ""
    version = data.get("best_oa_location", {}) or {}

    if isinstance(version, dict):
        url_for_pdf = version.get("url_for_pdf") or version.get("url_for_landing_page") or ""
        best_url = url_for_pdf
    else:
        best_url = ""

    # Try landing page if no PDF URL
    if not best_url:
        landing = data.get("best_oa_location", {}) or {}
        if isinstance(landing, dict):
            best_url = landing.get("url_for_landing_page", "") or ""

    return {
        "oa": is_oa,
        "best_url": best_url,
        "oa_status": oa_status,
        "license": license_,
        "version": version.get("version", "") if isinstance(version, dict) else "",
        "doi": doi,
    }


def get_oa_for_dois(dois: List[str]) -> Dict[str, Dict]:
    """Bulk lookup OA PDF URLs for a list of DOIs.

    More efficient than calling get_oa_pdf_url() in a loop.
    Unpaywall accepts up to 1000 DOIs per request.
    """
    email = _get_email()

    # Send the DOIs as a JSON list, not a newline-joined string.
    payload = json.dumps({"dois": list(dois)}).encode("utf-8")
    url = f"{UNPAYWALL_API}/bulk"

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "User-Agent": "Hermy-Research/1.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            results_raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {doi: {"error": f"Bulk Unpaywall failed: {e}", "oa": False} for doi in dois}

    # Results is a list; key by DOI
    output = {}
    for item in results_raw:
        if not isinstance(item, dict):
            continue
        doi = item.get("doi", "") or ""
        is_oa = item.get("is_oa", False)
        version = item.get("best_oa_location", {}) or {}
        if isinstance(version, dict):
            best_url = version.get("url_for_pdf") or version.get("url_for_landing_page") or ""
        else:
            best_url = ""
        output[doi] = {
            "oa": is_oa,
            "best_url": best_url,
            "oa_status": item.get("oa_status", "unknown") or "unknown",
            "license": item.get("license", "") or "",
            "version": version.get("version", "") if isinstance(version, dict) else "",
            "doi": doi,
        }

    return output


def enrich_article_with_oa(article: Dict) -> Dict:
    """Given an article dict with a DOI, enrich it with Unpaywall OA data.

    Adds fields: oa, oa_pdf_url, oa_status, license, oa_version.
    """
    doi = article.get("doi", "")
    if not doi:
        return article

    oa_info = get_oa_pdf_url(doi)
    if oa_info.get("error"):
        return article

    article["oa"] = oa_info.get("oa", False)
    article["oa_pdf_url"] = oa_info.get("best_url", "") or article.get("oa_pdf_url", "")
    article["oa_status"] = oa_info.get("oa_status", "unknown")
    article["license"] = oa_info.get("license", "")
    article["oa_version"] = oa_info.get("version", "")
    return article
