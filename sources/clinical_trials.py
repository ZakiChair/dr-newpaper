"""ClinicalTrials.gov source — registry of clinical trials worldwide.
API docs: https://clinicaltrials.gov/data-api/api
Free, no key required. Excellent for: ongoing trials, trial protocols,
intervention studies (crucial for medical research).
"""

import urllib.request
import urllib.parse
import json
import time
from typing import List, Dict

CTGOV_API = "https://clinicaltrials.gov/api/v2"


def search_trials(query: str, max_results: int = 5) -> List[Dict]:
    """Search ClinicalTrials.gov by query, return list of trial dicts.

    ClinicalTrials.gov covers: 450k+ clinical trials worldwide.
    Includes: study title, description, eligibility criteria,
    intervention types, enrollment, status, locations.
    """
    params = {
        "query.term": query,
        "pageSize": min(max_results, 20),
        "format": "json",
    }
    url = f"{CTGOV_API}/studies?" + urllib.parse.urlencode(params)

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
        return [{"error": f"ClinicalTrials.gov search failed: {e}"}]

    studies = data.get("studies", []) or []
    results = []

    for study in studies:
        if not isinstance(study, dict):
            continue

        # Protocol section contains key info
        protocol = study.get("protocolSection", {}) or {}

        # Identification module
        ident = protocol.get("identificationModule", {}) or {}
        nct_id = ident.get("nctId", "") or ""  # NCT ID
        title = ident.get("briefTitle", "") or ident.get("officialTitle", "") or "Unknown"
        org_id = ident.get("organization", {}).get("fullName", "") or ""

        # Status module
        status_mod = protocol.get("statusModule", {}) or {}
        status = status_mod.get("overallStatus", "") or ""
        enrollment = status_mod.get("enrollmentInfo", {}).get("count", "") or ""

        # Description module
        desc_mod = protocol.get("descriptionModule", {}) or {}
        abstract = desc_mod.get("briefSummary", "") or desc_mod.get("detailedDescription", "") or ""

        # Conditions
        conditions = protocol.get("conditionsModule", {}).get("conditions", []) or []
        condition = conditions[0] if conditions else ""

        # Interventions
        interv_mod = protocol.get("armsInterventionsModule", {}) or {}
        interventions = interv_mod.get("interventions", []) or []
        intervention_types = list({i.get("type", "") for i in interventions if isinstance(i, dict)}) or []

        # Eligibility
        elig_mod = protocol.get("eligibilityModule", {}) or {}
        criteria = elig_mod.get("eligibilityCriteria", "") or ""
        min_age = elig_mod.get("minimumAge", "") or ""
        max_age = elig_mod.get("maximumAge", "") or ""
        gender = elig_mod.get("sex", "") or ""

        # Phases
        design_mod = protocol.get("designModule", {}) or {}
        phases = design_mod.get("phases", []) or []

        # Locations
        contacts_mod = protocol.get("contactsLocationsModule", {}) or {}
        locations = contacts_mod.get("locations", []) or []
        countries = list({loc.get("country", "") for loc in locations if isinstance(loc, dict)}) or []

        # URL
        url_ref = f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""

        results.append({
            "source": "ClinicalTrials.gov",
            "title": title,
            "abstract": abstract[:800] if abstract else "",
            "date": status_mod.get("startDateStruct", {}).get("date", "") or "",
            "url": url_ref,
            "authors": [org_id] if org_id else [],
            "doi": "",  # Trials don't have DOIs
            "journal": "",
            "type": "clinical_trial",
            "nct_id": nct_id,
            "status": status,
            "enrollment": str(enrollment),
            "condition": condition,
            "intervention_types": intervention_types,
            "phases": phases,
            "criteria": criteria[:500] if criteria else "",
            "min_age": min_age,
            "max_age": max_age,
            "gender": gender,
            "countries": countries,
            "is_preprint": False,
        })

        time.sleep(0.1)

    return results


def get_trial_by_nct(nct_id: str) -> Dict:
    """Fetch a single clinical trial by NCT ID."""
    url = f"{CTGOV_API}/studies/{nct_id}?format=json"

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
        return {"error": f"ClinicalTrials.gov lookup failed: {e}"}

    protocol = data.get("protocolSection", {}) or {}
    ident = protocol.get("identificationModule", {}) or {}
    status_mod = protocol.get("statusModule", {}) or {}
    desc_mod = protocol.get("descriptionModule", {}) or {}

    return {
        "source": "ClinicalTrials.gov",
        "title": ident.get("briefTitle", "") or "Unknown",
        "abstract": desc_mod.get("briefSummary", "") or "",
        "url": f"https://clinicaltrials.gov/study/{nct_id}",
        "nct_id": nct_id,
        "status": status_mod.get("overallStatus", "") or "",
        "condition": protocol.get("conditionsModule", {}).get("conditions", [""])[0] or "",
    }
