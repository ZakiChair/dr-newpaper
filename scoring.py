"""Evidence, novelty, relevance, methodology and risk scoring for Dr_NewPaper."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Mapping, Any


def _year(article: Mapping[str, Any]) -> int | None:
    for key in ("publication_date", "date", "year"):
        match = re.search(r"(19|20)\d{2}", str(article.get(key) or ""))
        if match:
            return int(match.group(0))
    return None


def _text(article: Mapping[str, Any]) -> str:
    return " ".join(str(article.get(k) or "") for k in ("title", "abstract", "type", "article_type", "source", "journal", "summary", "deep_summary")).lower()


def _extract_funders(text: str) -> list[str]:
    funders = []
    patterns = [
        r"fund(?:ed|ing)? (?:was )?(?:provided |supported )?by ([A-Z][A-Za-z0-9& .-]{2,60})",
        r"supported by ([A-Z][A-Za-z0-9& .-]{2,60})",
        r"sponsored by ([A-Z][A-Za-z0-9& .-]{2,60})",
        r"grant from ([A-Z][A-Za-z0-9& .-]{2,60})",
    ]
    raw = " ".join(str(text or "").split())
    for pat in patterns:
        for match in re.findall(pat, raw):
            cleaned = re.split(r"\b(and|with|;|\.|,| participated| report| this | the )\b", match, flags=re.I)[0].strip()
            if cleaned and cleaned not in funders:
                funders.append(cleaned)
    return funders[:5]


def evaluate_article_deep(article: Mapping[str, Any]) -> dict:
    """Return a structured critical appraisal, not just a shallow score.

    The goal is to expose why a paper is useful or fragile: design, blinding,
    comparator, endpoint quality, statistical reporting, funding/COI, bias
    domains, limitations and a human-readable interpretation.
    """
    text = _text(article)
    raw_text = " ".join(str(article.get(k) or "") for k in ("title", "abstract", "summary", "deep_summary"))
    red_flags: list[str] = []

    def hits(terms: list[str]) -> list[str]:
        return [term for term in terms if term in text]

    if any(x in text for x in ["randomized", "randomised", "rct"]):
        design = "randomized_controlled_trial"
    elif "systematic review" in text or "meta-analysis" in text or "meta analysis" in text:
        design = "systematic_review_or_meta_analysis"
    elif "cohort" in text:
        design = "cohort"
    elif "case-control" in text or "case control" in text:
        design = "case_control"
    elif "case report" in text or "case series" in text:
        design = "case_report_or_series"
    else:
        design = "unclear"

    if "double-blind" in text or "double blind" in text:
        blinding = "double_blind"
    elif "single-blind" in text or "single blind" in text:
        blinding = "single_blind"
    elif "open-label" in text or "open label" in text:
        blinding = "open_label"
        red_flags.append("open_label_design")
    elif "blind" in text:
        blinding = "blinded_unclear"
    else:
        blinding = "not_reported"

    if "placebo" in text:
        control = "placebo_controlled"
    elif "uncontrolled" in text:
        control = "uncontrolled"
        red_flags.append("uncontrolled_design")
    elif "control group" in text or "controlled" in text:
        control = "controlled"
    else:
        control = "not_reported"

    randomization = "reported" if any(x in text for x in ["random sequence", "randomization", "randomisation", "randomly assigned", "computer-generated random"]) else ("mentioned" if any(x in text for x in ["randomized", "randomised", "rct"]) else "not_reported")
    allocation = "reported" if any(x in text for x in ["allocation concealment", "concealed allocation", "sealed envelope", "central randomization"]) else "not_reported"
    if design == "randomized_controlled_trial" and randomization == "mentioned":
        red_flags.append("randomization_method_unclear")
    if design == "randomized_controlled_trial" and allocation == "not_reported":
        red_flags.append("allocation_concealment_not_reported")

    n_match = re.search(r"\bn\s*[=:]\s*(\d{2,6})\b|\b(\d{2,6})\s+(patients|participants|subjects)\b", text)
    sample_size = int(next(g for g in n_match.groups() if g and g.isdigit())) if n_match else None
    if sample_size is not None and sample_size < 50:
        red_flags.append("small_sample")

    funders = _extract_funders(raw_text)
    funding_detected = bool(funders) or any(x in text for x in ["funded by", "funding", "sponsored by", "grant from"])
    sponsor_involved = any(x in text for x in ["sponsor participated", "sponsor was involved", "sponsor designed", "sponsor participated in study design", "sponsor participated in data analysis"])
    if sponsor_involved:
        red_flags.append("sponsor_involved_in_analysis")

    population_terms = ["patients", "participants", "subjects", "adults", "children", "women", "men", "human"]
    intervention_terms = ["treatment", "therapy", "intervention", "dose", "drug", "placebo", "minoxidil", "oxandrolone", "randomized"]
    comparator_terms = ["placebo", "control", "versus", "compared with", "standard care", "usual care"]
    study_population = [x for x in population_terms if x in text]
    interventions = [x for x in intervention_terms if x in text]
    comparators = [x for x in comparator_terms if x in text]

    coi_terms = ["consulting fees", "honoraria", "stock ownership", "employee of", "conflict of interest", "competing interests", "advisory board"]
    conflict_hits = hits(coi_terms)
    conflict_score = 0
    if conflict_hits:
        conflict_score += 20
    if any(x in text for x in ["stock ownership", "employee of"]):
        conflict_score += 15
    if sponsor_involved:
        conflict_score += 25
    if funding_detected and any(x in text for x in ["pharma", "therapeutics", "inc", "corp", "ltd"]):
        conflict_score += 10
    conflict_score = min(100, conflict_score)

    relevance = 0
    if any(x in text for x in ["patient", "human", "clinical"]):
        relevance += 25
    outcome_terms = ["mortality", "survival", "hospitalization", "safety", "efficacy", "diagnosis", "symptom", "quality of life", "adverse"]
    detected_outcomes = hits(outcome_terms)
    if detected_outcomes:
        relevance += 25
    if sample_size and sample_size >= 100:
        relevance += 20
    if design in {"randomized_controlled_trial", "systematic_review_or_meta_analysis"}:
        relevance += 20
    relevance = min(100, relevance)

    if any(x in text for x in ["mortality", "survival", "hospitalization", "quality of life"]):
        endpoint_type = "clinical_endpoint"
    elif any(x in text for x in ["biomarker", "surrogate", "laboratory", "imaging endpoint"]):
        endpoint_type = "surrogate_endpoint"
        red_flags.append("surrogate_endpoint")
    elif detected_outcomes:
        endpoint_type = "reported_outcome_unclear_type"
    else:
        endpoint_type = "not_reported"

    expanded_outcomes = outcome_terms + ["remission", "response", "relapse", "progression", "pain", "biomarker", "blood pressure", "glucose"]
    primary_outcomes: list[str] = []
    for term in expanded_outcomes:
        if term in text and term not in primary_outcomes:
            primary_outcomes.append(term)
    primary_outcome_reported = bool(re.search(r"primary (end ?point|outcome)", text))
    secondary_outcome_reported = bool(re.search(r"secondary (end ?point|outcome)", text))

    statistical_terms = ["confidence interval", "95% ci", "hazard ratio", "odds ratio", "relative risk", "p<", "p =", "p-value", "intention-to-treat", "per-protocol", "power calculation"]
    statistics_hits = hits(statistical_terms)
    effect_size_reported = any(x in text for x in ["hazard ratio", "odds ratio", "relative risk", "mean difference", "risk ratio"])
    ci_reported = any(x in text for x in ["confidence interval", "95% ci"])
    if primary_outcome_reported and not effect_size_reported:
        red_flags.append("effect_size_not_detected")
    if primary_outcome_reported and not ci_reported:
        red_flags.append("confidence_interval_not_detected")

    bias_domains: list[str] = []
    if allocation == "not_reported" and design == "randomized_controlled_trial":
        bias_domains.append("selection_bias_unclear")
    if blinding in {"open_label", "not_reported"} and design in {"randomized_controlled_trial", "cohort", "case_control"}:
        bias_domains.append("performance_detection_bias_unclear")
    if any(x in text for x in ["lost to follow-up", "dropout", "withdrawn", "attrition"]):
        bias_domains.append("attrition_bias_signal")
    if any(x in text for x in ["post hoc", "exploratory endpoint", "multiple comparisons"]):
        bias_domains.append("selective_reporting_or_multiplicity_signal")
    if any(x in text for x in ["retrospective", "registry", "database study"]):
        bias_domains.append("observational_confounding_risk")

    limitations: list[str] = []
    if sample_size is None:
        limitations.append("sample_size_not_detected")
    elif sample_size < 50:
        limitations.append("small_sample")
    if control == "not_reported":
        limitations.append("comparator_not_clear")
    if endpoint_type == "not_reported":
        limitations.append("outcomes_not_clear")
    if not statistics_hits:
        limitations.append("statistical_reporting_not_detected")

    completeness_fields = {
        "design": design != "unclear",
        "sample_size": sample_size is not None,
        "population": bool(study_population),
        "intervention": bool(interventions),
        "comparator": control != "not_reported" or bool(comparators),
        "outcomes": bool(primary_outcomes),
        "statistics": bool(statistics_hits) or primary_outcome_reported,
        "funding_or_coi": funding_detected or bool(conflict_hits),
        "date": _year(article) is not None,
        "doi_or_url": bool(article.get("doi") or article.get("url")),
    }
    completeness_score = round(sum(1 for ok in completeness_fields.values() if ok) / len(completeness_fields) * 100)

    if design == "randomized_controlled_trial":
        grade_level = "high"
    elif design == "systematic_review_or_meta_analysis":
        grade_level = "high"
    elif design in {"cohort", "case_control"}:
        grade_level = "low"
    elif design == "case_report_or_series":
        grade_level = "very_low"
    else:
        grade_level = "very_low"
    downgrade_reasons = []
    if bias_domains:
        downgrade_reasons.append("risk_of_bias")
    if sample_size is not None and sample_size < 100:
        downgrade_reasons.append("imprecision")
    if endpoint_type in {"surrogate_endpoint", "not_reported"}:
        downgrade_reasons.append("indirectness")
    if conflict_score >= 40:
        downgrade_reasons.append("industry_or_coi_concern")

    quality_score = 45
    quality_score += {"systematic_review_or_meta_analysis": 22, "randomized_controlled_trial": 25, "cohort": 10, "case_control": 4, "case_report_or_series": -18, "unclear": -8}.get(design, 0)
    if blinding == "double_blind":
        quality_score += 10
    if control == "placebo_controlled":
        quality_score += 8
    if randomization == "reported":
        quality_score += 7
    if allocation == "reported":
        quality_score += 7
    if sample_size and sample_size >= 100:
        quality_score += 7
    if effect_size_reported:
        quality_score += 5
    if ci_reported:
        quality_score += 5
    quality_score -= min(30, len(red_flags) * 4 + len(bias_domains) * 5)
    quality_score = max(0, min(100, quality_score))

    interpretation_bits = []
    if design == "randomized_controlled_trial" and blinding == "double_blind" and control == "placebo_controlled":
        interpretation_bits.append("methodologically strong RCT structure")
    if funding_detected:
        interpretation_bits.append("funding disclosed or suspected")
    if conflict_score >= 40:
        interpretation_bits.append("material conflict-of-interest risk")
    if endpoint_type == "surrogate_endpoint":
        interpretation_bits.append("results may rely on surrogate endpoints")
    if bias_domains:
        interpretation_bits.append("bias domains require manual review: " + ", ".join(bias_domains[:3]))
    if limitations:
        interpretation_bits.append("missing/incomplete reporting: " + ", ".join(limitations[:3]))
    interpretation = "; ".join(interpretation_bits) or "limited information; interpret cautiously"

    return {
        "methodology": {
            "study_design": design,
            "blinding": blinding,
            "control": control,
            "randomization": randomization,
            "allocation_concealment": allocation,
            "sample_size": sample_size,
        },
        "funding": {
            "funding_detected": funding_detected,
            "suspected_funders": funders,
            "sponsor_involvement": sponsor_involved,
        },
        "conflicts": {
            "conflict_risk_score": conflict_score,
            "signals": conflict_hits,
        },
        "relevance": {
            "pertinence_score": relevance,
            "human_or_clinical": any(x in text for x in ["patient", "human", "clinical"]),
            "clinical_outcomes": [x for x in ["safety", "efficacy", "mortality", "diagnosis", "adverse"] if x in text],
        },
        "outcomes": {
            "primary_outcomes": primary_outcomes[:10],
            "primary_outcome_reported": primary_outcome_reported,
            "secondary_outcome_reported": secondary_outcome_reported,
            "endpoint_type": endpoint_type,
        },
        "statistics": {
            "effect_size_reported": effect_size_reported,
            "confidence_interval_reported": ci_reported,
            "signals": statistics_hits,
        },
        "bias": {
            "domains": bias_domains,
            "overall_risk": "high" if len(bias_domains) >= 3 or conflict_score >= 45 else ("some_concerns" if bias_domains or conflict_score >= 20 else "low_or_not_detected"),
        },
        "limitations": limitations,
        "completeness": {
            "score": completeness_score,
            "fields": completeness_fields,
            "missing_fields": [k for k, ok in completeness_fields.items() if not ok],
        },
        "pico": {
            "study_population": study_population,
            "intervention_signals": interventions,
            "comparator_signals": comparators,
            "outcome_signals": primary_outcomes[:10],
        },
        "evidence_grade": {
            "framework": "GRADE-inspired heuristic",
            "level": grade_level,
            "downgrade_reasons": downgrade_reasons,
        },
        "reporting_quality": {
            "frameworks_considered": ["CONSORT", "STROBE", "PRISMA", "GRADE"],
            "consort_signals": [x for x in ["randomization", "allocation concealment", "blinding", "intention-to-treat"] if x in text],
            "reporting_gaps": limitations,
        },
        "quality_score": quality_score,
        "interpretation": interpretation,
        "red_flags": red_flags,
    }


def score_article(article: Mapping[str, Any]) -> dict:
    text = _text(article)
    explanation = []
    deep = evaluate_article_deep(article)

    evidence = 8
    design = deep["methodology"]["study_design"]
    if design == "systematic_review_or_meta_analysis":
        evidence = 42; explanation.append("high evidence design: systematic review/meta-analysis")
    elif design == "randomized_controlled_trial":
        evidence = 38; explanation.append("strong evidence design: randomized/clinical trial")
    elif design == "cohort":
        evidence = 24; explanation.append("observational cohort evidence")
    elif design == "case_control":
        evidence = 18; explanation.append("case-control evidence")
    elif design == "case_report_or_series":
        evidence = 8; explanation.append("low evidence design: case report/series")

    if deep["methodology"]["blinding"] == "double_blind":
        evidence += 8; explanation.append("double-blind methodology")
    elif deep["methodology"]["blinding"] == "open_label":
        evidence -= 6; explanation.append("open-label design")
    if deep["methodology"]["control"] == "placebo_controlled":
        evidence += 6; explanation.append("placebo-controlled")
    elif deep["methodology"]["control"] == "uncontrolled":
        evidence -= 8; explanation.append("uncontrolled design")
    evidence = max(0, min(55, evidence))

    novelty = 6
    year = _year(article)
    current_year = datetime.now(timezone.utc).year
    if year:
        age = current_year - year
        if age <= 0:
            novelty = 30; explanation.append("very recent publication")
        elif age <= 1:
            novelty = 22; explanation.append("recent publication")
        elif age <= 3:
            novelty = 12
        else:
            novelty = 4

    clinical = min(35, deep["relevance"]["pertinence_score"] // 3 + 5)
    if deep.get("outcomes", {}).get("endpoint_type") == "clinical_endpoint":
        clinical = min(35, clinical + 8); explanation.append("patient-important clinical endpoint")
    elif deep.get("outcomes", {}).get("endpoint_type") == "surrogate_endpoint":
        clinical = max(0, clinical - 5); explanation.append("surrogate endpoint")
    if deep["methodology"]["sample_size"]:
        explanation.append("sample size signal detected")

    citation_raw = article.get("citation_count") or article.get("citations") or 0
    try:
        citation = min(int(citation_raw) // 25, 20)
    except Exception:
        citation = 0

    risk = 0
    if any(x in text for x in ["preprint", "medrxiv", "biorxiv"]):
        risk += 18; explanation.append("preprint / not necessarily peer reviewed")
    if not article.get("abstract"):
        risk += 10; explanation.append("abstract missing")
    if not article.get("doi"):
        risk += 4; explanation.append("DOI missing")
    if any(x in text for x in ["retracted", "expression of concern"]):
        risk += 100; explanation.append("retraction risk flag")
    if any(x in text for x in ["small", "pilot", "uncontrolled"]):
        risk += 7; explanation.append("small/uncontrolled study signal")
    risk += min(35, deep["conflicts"]["conflict_risk_score"] // 2)
    risk += min(25, len(deep.get("bias", {}).get("domains", [])) * 5)
    if deep.get("quality_score", 50) < 35:
        risk += 8; explanation.append("low methodology quality score")
    if deep["funding"]["sponsor_involvement"]:
        explanation.append("sponsor involvement in design/analysis")
    if deep["conflicts"]["signals"]:
        explanation.append("conflict-of-interest signals detected")

    final = max(0, min(100, evidence + novelty + clinical + citation - risk))
    if risk >= 25 or final < 50:
        label = "RISK"
    elif final >= 80:
        label = "HOT"
    elif novelty >= 20:
        label = "NEW"
    else:
        label = "WATCH"

    return {
        "novelty_score": novelty,
        "evidence_score": evidence,
        "citation_score": citation,
        "clinical_relevance_score": clinical,
        "risk_score": risk,
        "final_score": final,
        "label": label,
        "explanation": explanation,
        "deep_evaluation": deep,
    }
