"""Structured summary parsing and prompting for Dr_NewPaper."""
from __future__ import annotations

import json
import re
from typing import Any

from config import DEFAULT_MODEL

STRUCTURED_SUMMARY_SCHEMA = {
    "study_type": "unknown",
    "population": "",
    "sample_size": "",
    "intervention": "",
    "comparator": "",
    "primary_outcome": "",
    "key_findings": [],
    "effect_sizes": [],
    "limitations": [],
    "clinical_implications": "",
    "evidence_grade": "unknown",
    "red_flags": [],
    "confidence": 0.0,
}


def structured_summary_prompt(article: dict, lang: str = "fr") -> str:
    return f"""Tu es un analyste senior de littérature scientifique.
Retourne UNIQUEMENT un JSON valide, sans markdown, avec les clés suivantes:
{json.dumps(STRUCTURED_SUMMARY_SCHEMA, ensure_ascii=False, indent=2)}

Règles:
- Utilise seulement le titre/abstract/texte fourni.
- N'invente jamais de statistiques.
- Si une donnée manque, laisse une chaîne vide ou [].
- evidence_grade doit être low, moderate, high ou unknown.
- confidence est un nombre entre 0 et 1.
- Langue des champs textuels: {lang}.

ARTICLE:
Title: {article.get('title','')}
Source: {article.get('source','')}
Date: {article.get('date') or article.get('publication_date','')}
Journal: {article.get('journal','')}
DOI: {article.get('doi','')}
Abstract/Text:
{(article.get('deep_summary') or article.get('abstract') or article.get('summary') or '')[:12000]}
"""


def parse_structured_summary(text: str) -> dict[str, Any]:
    raw = text or ""
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.I)
    if match:
        raw = match.group(1)
    else:
        brace = raw.find("{")
        last = raw.rfind("}")
        if brace >= 0 and last > brace:
            raw = raw[brace:last + 1]
    try:
        parsed = json.loads(raw)
    except Exception:
        return {**STRUCTURED_SUMMARY_SCHEMA, "raw_parse_error": text}
    if not isinstance(parsed, dict):
        return {**STRUCTURED_SUMMARY_SCHEMA, "raw_parse_error": text}
    output = dict(STRUCTURED_SUMMARY_SCHEMA)
    output.update(parsed)
    return output
