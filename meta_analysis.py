#!/usr/bin/env python3
"""Meta-analysis module for Dr_NewPaper.

Compiles a *complete, readable* narrative meta-analysis document from many
relevant studies and lets MiniMax extract the overall trend. Depth is
configurable by the caller (number of studies + abstract-only vs full-text PDF
enrichment). All model calls go through :func:`minimax_client.chat`, so the
`<think>`-budget bug that used to return empty summaries is handled in one place.
"""

import time
import logging
from typing import Dict, List, Optional, Callable

import minimax_client

DEFAULT_MODEL = minimax_client.DEFAULT_MODEL


def _call(prompt: str, model: str = DEFAULT_MODEL, temperature: float = 0.3,
          max_tokens: int = 2000, system: Optional[str] = None) -> str:
    """Backward-compatible thin wrapper around the unified MiniMax client."""
    return minimax_client.chat(prompt, system=system, model=model,
                               temperature=temperature, max_tokens=max_tokens)


# --------------------------------------------------------------------------- #
# Source search (best-effort; each source is optional / may be missing)
# --------------------------------------------------------------------------- #
def _safe_search(module: str, fn_name: str, query: str, max_results: int) -> List[Dict]:
    try:
        mod = __import__(f"sources.{module}", fromlist=[fn_name])
        return getattr(mod, fn_name)(query, max_results=max_results) or []
    except Exception as e:  # noqa: BLE001 — a dead source must not kill the run
        logging.warning("%s search failed: %s", module, e)
        return []


def search_europe_pmc(query: str, max_results: int = 8) -> List[Dict]:
    return _safe_search("europe_pmc", "search_europe_pmc", query, max_results)


def search_pubmed(query: str, max_results: int = 8) -> List[Dict]:
    return _safe_search("pubmed", "search_pubmed", query, max_results)


def search_crossref(query: str, max_results: int = 8) -> List[Dict]:
    return _safe_search("crossref", "search_crossref", query, max_results)


def search_openalex(query: str, max_results: int = 8) -> List[Dict]:
    return _safe_search("openalex", "search_openalex", query, max_results)


_SEARCHER_MAP = {
    "pubmed": search_pubmed,
    "crossref": search_crossref,
    "openalex": search_openalex,
    "europe_pmc": search_europe_pmc,
}
_ALL_SOURCES = list(_SEARCHER_MAP.keys())


def _dedup(articles: List[Dict], max_articles: int = 8) -> List[Dict]:
    seen, result = set(), []
    for a in articles:
        if a.get("error"):
            continue
        abstract = (a.get("abstract") or "").strip()
        if not abstract:
            continue
        doi = a.get("doi", "")
        if doi and doi in seen:
            continue
        if doi:
            seen.add(doi)
        result.append(a)
        if len(result) >= max_articles:
            break
    return result


def _build_context(articles: List[Dict], lang: str = "fr", abstract_chars: int = 900) -> str:
    lt = {"en": "Title"}.get(lang, "Titre")
    la = {"en": "Authors", "ar": "المؤلفون"}.get(lang, "Auteurs")
    ld = {"en": "Date"}.get(lang, "Date")
    lab = {"en": "Abstract", "ar": "الملخص"}.get(lang, "Résumé")
    lj = {"en": "Journal"}.get(lang, "Revue")

    blocks = []
    for i, a in enumerate(articles, 1):
        title = (a.get("title") or "Unknown")[:200]
        authors = ", ".join(a.get("authors", []) or ["Unknown"])[:120]
        date = (a.get("date") or a.get("publication_date") or "Unknown")[:10]
        journal = (a.get("journal") or "")[:120]
        abstract = (a.get("full_text_excerpt") or a.get("abstract") or "")[:abstract_chars]
        doi = a.get("doi", "")
        blocks.append(
            f"[Article {i}]\n{lt}: {title}\n{la}: {authors}\n{ld}: {date}\n"
            f"{lj}: {journal}\nDOI: {doi}\n{lab}: {abstract}"
        )
    return "\n\n".join(blocks)


_DEPTH_SETTINGS = {
    "low":    {"abstract_chars": 500,  "max_tokens": 1500},
    "medium": {"abstract_chars": 900,  "max_tokens": 4000},
    "deep":   {"abstract_chars": 4000, "max_tokens": 6000},
}


def _low_document_prompt(query: str, context: str, n: int, lang: str) -> str:
    if lang == "en":
        return f"""You are a medical researcher. Write a BRIEF meta-analysis overview for "{query}" ({n} studies).

# Quick Overview: {query}

## Study table
Markdown table: # | Study (author, year) | Design | N | Key finding

## Main findings
3-5 bullet points with the dominant conclusions. Cite [Article k].

## Limitations
2-3 bullet points on evidence quality.

RULES: English only. Use ONLY the {n} studies below. Max 400 words. NEVER invent statistics.

STUDIES:
{context}

OVERVIEW:"""
    return f"""Tu es un chercheur médical. Rédige un BREF aperçu de méta-analyse pour "{query}" ({n} études).

# Aperçu rapide : {query}

## Tableau des études
Tableau Markdown : # | Étude (auteur, année) | Schéma | N | Résultat clé

## Résultats principaux
3-5 puces avec les conclusions dominantes. Cite [Article k].

## Limites
2-3 puces sur la qualité des preuves.

RÈGLES : Français uniquement. Utilise UNIQUEMENT les {n} études ci-dessous. Max 400 mots. N'invente jamais de statistiques.

ÉTUDES :
{context}

APERÇU :"""


def _deep_document_prompt(query: str, context: str, n: int, lang: str, full_text: bool) -> str:
    ft_note_fr = (" Tous les articles incluent des résumés détaillés extraits du texte intégral." if full_text else "")
    ft_note_en = (" All articles include detailed full-text summaries." if full_text else "")
    if lang == "en":
        return f"""You are a senior medical researcher and systematic review author.
Write a COMPLETE, PUBLICATION-QUALITY narrative meta-analysis for "{query}" ({n} studies).{ft_note_en}

# Systematic Meta-Analysis: {query}

## 1. Introduction & clinical question
PICO framework if applicable. 4-6 sentences framing the question.

## 2. Methods & included studies
Databases searched, inclusion/exclusion criteria.
Full Markdown table: # | Author (year) | Design | N | Population | Intervention | Outcome | Key result | Risk of bias

## 3. Results by theme (detailed)
3-5 themes. For each: all relevant studies with specific numbers (effect sizes, CIs, p-values, ORs/RRs/HRs) [Article k]. Note consistency or disagreement.

## 4. Quantitative synthesis
Direction of effect across studies, magnitude, consistency. Estimate heterogeneity (I² if computable). Describe what a forest plot would show.

## 5. Evidence quality (GRADE)
For each major outcome: Very Low / Low / Moderate / High, with downgrading reasons.

## 6. Risk of bias
Blinding, allocation concealment, attrition, reporting bias. Potential publication bias.

## 7. Clinical implications
5-6 strength-of-evidence-graded sentences for practice.

## 8. Limitations of this meta-analysis
4-6 bullet points: heterogeneity, missing RCTs, confounders, language bias, small samples.

## 9. Research gaps & future directions
3-4 concrete research gaps.

## 10. Conclusion
3-4 sentences: main finding, level of certainty, clinical take-away.

RULES: English. Cite exclusively from the {n} studies. NEVER invent statistics — say "not reported" if absent.
This must be exhaustive, rigorous, and ready for peer review.

STUDIES:
{context}

META-ANALYSIS:"""
    return f"""Tu es un chercheur médical senior et auteur de revues systématiques.
Rédige une méta-analyse narrative COMPLÈTE et de QUALITÉ PUBLICATION pour "{query}" ({n} études).{ft_note_fr}

# Méta-analyse systématique : {query}

## 1. Introduction & question clinique
Cadre PICO si applicable. 4-6 phrases posant la question.

## 2. Méthodes & études incluses
Bases consultées, critères d'inclusion/exclusion.
Tableau Markdown complet : # | Auteur (année) | Schéma | N | Population | Intervention | Critère | Résultat clé | Risque de biais

## 3. Résultats par thème (détaillés)
3-5 thèmes. Pour chacun : toutes les études pertinentes avec chiffres précis (tailles d'effet, IC, valeurs p, OR/RR/HR) [Article k]. Convergences et divergences notées.

## 4. Synthèse quantitative
Direction d'effet, amplitude, cohérence entre études. Estime l'hétérogénéité (I² si calculable). Décris ce qu'un forest plot montrerait.

## 5. Qualité des preuves (GRADE)
Pour chaque critère principal : Très Faible / Faible / Modéré / Élevé, avec raisons de déclassement.

## 6. Risque de biais
Randomisation, insu, attrition, biais de déclaration. Biais de publication potentiel.

## 7. Implications cliniques
5-6 phrases de recommandations graduées selon le niveau de preuve.

## 8. Limites de cette méta-analyse
4-6 puces : hétérogénéité, ECR manquants, confondeurs, biais de langue, petits échantillons.

## 9. Lacunes & pistes de recherche
3-4 lacunes concrètes pour les futures études.

## 10. Conclusion
3-4 phrases : résultat principal, niveau de certitude, message clinique.

RÈGLES : Français. Cite exclusivement les {n} études. N'invente JAMAIS de statistiques — écris « non rapporté » si absent.
Cette méta-analyse doit être exhaustive, rigoureuse et prête à la révision par les pairs.

ÉTUDES :
{context}

MÉTA-ANALYSE :"""


def _document_prompt(query: str, context: str, n: int, lang: str, full_text: bool) -> str:
    ft_note_fr = (" Certains articles incluent des extraits de texte intégral (marqués dans le Résumé)." if full_text else "")
    ft_note_en = (" Some articles include full-text excerpts." if full_text else "")
    if lang == "en":
        return f"""You are a senior medical researcher writing a COMPLETE narrative meta-analysis document, based EXCLUSIVELY on the {n} studies provided below about "{query}".{ft_note_en}

Write a thorough, well-structured document in Markdown with these sections:

# Meta-analysis: {query}

## 1. Background & objective
2-4 sentences framing the clinical/scientific question.

## 2. Search & included studies
State that {n} studies were screened from PubMed/CrossRef/OpenAlex/Europe PMC. Add a Markdown table with columns: # | Study (first author, year) | Design | N / sample | Key result.

## 3. Synthesis of findings (by theme)
Group the evidence into 2-4 themes. For each theme, synthesize what the studies converge on, citing specific numbers/statistics ([Article k]).

## 4. Overall trend
One paragraph: what is the dominant direction of the evidence? Is it consistent or conflicting?

## 5. Heterogeneity & quality
Comment on study designs, sample sizes, consistency, and obvious risk-of-bias signals.

## 6. Clinical implications
2-3 sentences for practice.

## 7. Gaps & future research
2-3 concrete gaps.

## 8. Limitations of this meta-analysis
3-4 bullet points on intrinsic limitations: potential publication bias, study heterogeneity, missing data, generalizability.

## 9. Conclusion
2-3 sentences.

RULES: Write in English. Use ONLY the provided studies. Cite as [Article k]. NEVER invent statistics; if a number is absent, say "not reported".

STUDIES:
{context}

DOCUMENT:"""
    return f"""Tu es un chercheur médical senior qui rédige un DOCUMENT de méta-analyse narrative COMPLET, en te basant EXCLUSIVEMENT sur les {n} études fournies ci-dessous au sujet de "{query}".{ft_note_fr}

Rédige un document approfondi et bien structuré en Markdown avec ces sections :

# Méta-analyse : {query}

## 1. Contexte & objectif
2-4 phrases qui posent la question clinique/scientifique.

## 2. Recherche & études incluses
Précise que {n} études ont été examinées (PubMed/CrossRef/OpenAlex/Europe PMC). Ajoute un tableau Markdown : # | Étude (1er auteur, année) | Type d'étude | N / échantillon | Résultat clé.

## 3. Synthèse des résultats (par thème)
Regroupe les preuves en 2-4 thèmes. Pour chaque thème, synthétise les convergences en citant des chiffres/statistiques précis ([Article k]).

## 4. Tendance générale
Un paragraphe : quelle est la direction dominante des preuves ? Est-elle cohérente ou contradictoire ?

## 5. Hétérogénéité & qualité
Commente les types d'études, tailles d'échantillon, cohérence, et signaux évidents de risque de biais.

## 6. Implications cliniques
2-3 phrases pour la pratique.

## 7. Lacunes & recherches futures
2-3 lacunes concrètes.

## 8. Limites de cette méta-analyse
3-4 puces sur les limites intrinsèques : biais de publication potentiel, hétérogénéité des études, données manquantes, généralisabilité.

## 9. Conclusion
2-3 phrases.

RÈGLES : Écris en français. Utilise UNIQUEMENT les études fournies. Cite sous la forme [Article k]. N'invente JAMAIS de statistiques ; si un chiffre est absent, écris « non rapporté ».

ÉTUDES :
{context}

DOCUMENT :"""


def _enrich_full_text(articles: List[Dict], limit: int, allow_scihub: Optional[bool],
                      progress: Optional[Callable[[str], None]] = None) -> None:
    """Best-effort: attach a `full_text_excerpt` to the top `limit` studies."""
    try:
        import pdf_sender
        from sources.scihub import parse_pdf_for_summary
    except Exception:  # noqa: BLE001
        return
    done = 0
    for a in articles:
        if done >= limit:
            break
        doi = a.get("doi") or ""
        if not doi:
            continue
        if progress:
            progress(f"PDF {done + 1}/{limit} — {(a.get('title') or '')[:40]}")
        try:
            dl = pdf_sender.download_pdf(doi, title=a.get("title") or "",
                                         known_urls=_known_urls(a), allow_scihub=allow_scihub)
            if dl.get("success") and dl.get("path"):
                text = parse_pdf_for_summary(dl["path"])
                if text and len(text) > 400:
                    a["full_text_excerpt"] = text[:4000]
                    done += 1
        except Exception:  # noqa: BLE001
            continue


def _minimax_study_summary(article: Dict, full_text: str, lang: str = "fr") -> str:
    """Ask MiniMax for a detailed per-study summary (used in deep mode)."""
    title = (article.get("title") or "")[:200]
    if lang == "en":
        prompt = (
            f"Summarize this scientific study in detail (300-500 words).\n"
            f"Include: study design, population, N, intervention/exposure, key findings with "
            f"specific numbers (effect sizes, CIs, p-values), methodological limitations, conclusion.\n\n"
            f"Study: {title}\n\nText:\n{full_text[:5000]}\n\nSUMMARY:"
        )
    else:
        prompt = (
            f"Résume cette étude scientifique en détail (300-500 mots).\n"
            f"Inclus : schéma d'étude, population, N, intervention/exposition, résultats clés avec "
            f"chiffres précis (tailles d'effet, IC, valeurs p), limites méthodologiques, conclusion.\n\n"
            f"Étude : {title}\n\nTexte :\n{full_text[:5000]}\n\nRÉSUMÉ :"
        )
    try:
        return _call(prompt, max_tokens=700, temperature=0.2)
    except Exception:  # noqa: BLE001
        return ""


def _deep_enrich_studies(articles: List[Dict], limit: int, allow_scihub: Optional[bool],
                         lang: str = "fr",
                         progress: Optional[Callable[[str], None]] = None) -> None:
    """Deep mode: download every PDF then get a detailed MiniMax summary per study."""
    _enrich_full_text(articles, limit=limit, allow_scihub=allow_scihub, progress=progress)
    done = 0
    enriched = [a for a in articles if len(a.get("full_text_excerpt") or "") >= 300]
    for a in enriched:
        if progress:
            progress(f"MiniMax {done + 1}/{len(enriched)} — {(a.get('title') or '')[:40]}")
        summary = _minimax_study_summary(a, a["full_text_excerpt"], lang)
        if summary and len(summary) > 100:
            a["full_text_excerpt"] = summary
        done += 1


def _known_urls(a: Dict) -> List[str]:
    return [u for u in (a.get("oa_pdf_url"), a.get("pdf_url"), a.get("url")) if u]


def perform_meta_analysis(query: str, max_articles: int = 8, deep: bool = False,
                          lang: str = "fr", *, sources: Optional[List[str]] = None,
                          full_text: Optional[bool] = None,
                          allow_scihub: Optional[bool] = None,
                          analysis_depth: str = "medium",
                          progress: Optional[Callable[[str], None]] = None) -> Dict:
    """Run a meta-analysis and compile a complete narrative document.

    Args:
        analysis_depth: "low" (brief abstract summary), "medium" (synthesis with
            numbers + limitations), or "deep" (exhaustive, all PDFs + per-study
            MiniMax enrichment, publication-quality 10-section report).
        max_articles: how many studies to include.
        sources: which databases to query (default: all four).
        deep / full_text: legacy flags, honoured when analysis_depth is not the
            driver. analysis_depth="deep" always implies full_text=True.
        allow_scihub: Sci-Hub fallback for PDF download.
        progress: optional callback for UI status lines.
    """
    depth_key = analysis_depth.lower() if analysis_depth.lower() in _DEPTH_SETTINGS else "medium"
    depth_cfg = _DEPTH_SETTINGS[depth_key]
    if full_text is None:
        full_text = (depth_key == "deep") or deep
    abstract_chars = depth_cfg["abstract_chars"]
    max_tok = depth_cfg["max_tokens"]

    active = [_SEARCHER_MAP[s] for s in (sources or _ALL_SOURCES) if s in _SEARCHER_MAP]
    if not active:
        active = list(_SEARCHER_MAP.values())

    if progress:
        progress("Recherche des études…")
    all_results: List[Dict] = []
    for searcher in active:
        all_results.extend(searcher(query, max_results=max_articles))

    articles = _dedup(all_results, max_articles=max_articles)
    if not articles:
        return {"query": query, "articles": [], "summary": "Aucun article trouvé avec abstract disponible.",
                "n_studies": 0, "lang": lang, "depth": depth_key}

    if full_text:
        if depth_key == "deep":
            _deep_enrich_studies(articles, limit=len(articles),
                                 allow_scihub=allow_scihub, lang=lang, progress=progress)
        else:
            _enrich_full_text(articles, limit=len(articles),
                              allow_scihub=allow_scihub, progress=progress)

    if progress:
        progress(f"Compilation du document ({len(articles)} études)…")
    context = _build_context(articles, lang=lang, abstract_chars=abstract_chars)
    if depth_key == "low":
        prompt = _low_document_prompt(query, context, len(articles), lang)
    elif depth_key == "deep":
        prompt = _deep_document_prompt(query, context, len(articles), lang, full_text)
    else:
        prompt = _document_prompt(query, context, len(articles), lang, full_text)
    summary = minimax_client.chat(prompt, max_tokens=max_tok, temperature=0.3, timeout=150)

    return {"query": query, "articles": articles, "summary": summary,
            "n_studies": len(articles), "lang": lang, "depth": depth_key}


def format_telegram_message(result: Dict, deep: bool = False) -> List[str]:
    query = result.get("query", "")
    n = result.get("n_studies", 0)
    summary = result.get("summary", "")
    lang = result.get("lang", "fr")
    articles = result.get("articles", [])

    lang_emoji = {"fr": "🇫🇷", "en": "🇬🇧", "ar": "🇸🇦"}.get(lang, "🌐")
    mode_tag = " [DEEP]" if (deep or result.get("depth") == "deep") else ""
    messages = [f"{lang_emoji} Meta-Analyse{mode_tag}\n Sujet : {query}\n {n} étude(s) analysée(s)\n━━━━━━━━━━━━━━━━━━━━"]
    if summary:
        # Telegram caps messages at 4096 chars; chunk the document.
        for i in range(0, len(summary), 3500):
            messages.append(summary[i:i + 3500])
    if articles:
        lines = ["Sources :"]
        for a in articles[:8]:
            title = (a.get("title") or "—")[:70]
            pmid = a.get("pmid", "")
            doi = a.get("doi", "")
            if pmid:
                lines.append(f"  [{pmid}] {title}")
            elif doi:
                lines.append(f"  DOI:{doi[:24]}… {title}")
            else:
                lines.append(f"  {title}")
        messages.append("\n".join(lines))
    return messages


if __name__ == "__main__":
    result = perform_meta_analysis("oxandrolone liver toxicity", max_articles=5, lang="fr")
    print(f"Studies: {result['n_studies']}")
    print(f"\n=== DOCUMENT ===\n{result['summary']}")
