"""Deep Research pipeline — --deep mode.

Workflow:
1. Search across PubMed + CrossRef + OpenAlex + Europe PMC + bio/medRxiv
   to collect articles with DOIs
2. For each article:
   a. Try to get OA PDF URL (Unpaywall → OpenAlex → Europe PMC → Sci-Hub fallback)
   b. Download and parse PDF (marker-pdf → PyMuPDF → pdfminer)
   c. Send full text to MiniMax for comprehensive summary
   d. Fall back to abstract if PDF download/parse fails
3. Return enriched results with deep summaries

Usage:
    python deep_research.py "quantum computing" --max 3 --lang fr
"""

import sys
import os
import time
import re
from pathlib import Path
from typing import List, Dict, Optional

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

import config
from sources.pubmed import search_pubmed
from sources.crossref import search_crossref
from sources.openalex import search_openalex
from sources.europe_pmc import search_europe_pmc
from sources.biorxiv import search_biorxiv, search_medrxiv
from sources.unpaywall import get_oa_pdf_url
from sources.scihub import try_download_pdf, parse_pdf_for_summary, get_scihub_url
from minimax_client import summarize_batch
from config import DEFAULT_MODEL


DEEP_SYSTEM_PROMPT = """Tu es un analyste de recherche académique spécialisé. Ta tâche est de produire un résumé structuré et approfondi d'un article scientifique à partir de son texte intégral (ou de son abstract si le texte complet n'est pas disponible).

Structure le résumé ainsi:
1. CONTEXTE & OBJECTIF (2-3 phrases): Quel problème scientifique cet article aborde-t-il ? Quelle est la question de recherche ?
2. MÉTHODOLOGIE (2-4 phrases): Comment les auteurs ont-ils procédé ? Quelle données, expériences, ou analyses ont-ils utilisées ?
3. RÉSULTATS PRINCIPAUX (3-5 phrases): Quelles sont les découvertes clés ? Utilisez des données spécifiques quand elles sont disponibles.
4. LIMITATIONS (1-2 phrases): Quelles sont les faiblesses ou limites reconnues par les auteurs ?
5. IMPLICATIONS (1-2 phrases): Pourquoi ces résultats comptent-ils ? Quelles applications ou recherches futures cela ouvre-t-il ?
6. ÉVALUATION CRITIQUE (4-6 phrases): Apprécie la qualité de la preuve. Couvre explicitement : la solidité du devis (RCT, cohorte, cas, revue…), les principaux risques de biais (sélection, performance, attrition, conflits d'intérêts/financement), la robustesse statistique (taille d'échantillon, intervalles de confiance, multiplicité), et l'applicabilité/généralisabilité des résultats.
7. NIVEAU DE PREUVE & VERDICT (1-2 phrases): Donne une certitude globale (Élevée / Modérée / Faible / Très faible) et un verdict en une ligne sur la fiabilité et l'utilité de l'étude.

Règles:
- En français, ton académique mais accessible
- 300-550 mots
- Cite les chiffres et données précis quand disponibles
- L'ÉVALUATION CRITIQUE et le VERDICT sont obligatoires, même brefs
- Si le texte est un abstract (pas le full text), ajoute la mention [basé sur l'abstract uniquement] et reste prudent dans l'évaluation
- Ne spécule pas au-delà de ce que dit le texte"""


ENGLISH_SYSTEM_PROMPT = """You are an academic research analyst. Produce a structured, in-depth summary of a scientific article from its full text (or abstract if full text is unavailable).

Structure:
1. CONTEXT & OBJECTIVE (2-3 sentences): What scientific problem does this article address? What is the research question?
2. METHODOLOGY (2-4 sentences): How did the authors proceed? What data, experiments, or analyses did they use?
3. KEY FINDINGS (3-5 sentences): What are the main discoveries? Use specific data when available.
4. LIMITATIONS (1-2 sentences): What weaknesses or caveats do the authors acknowledge?
5. IMPLICATIONS (1-2 sentences): Why do these results matter? What future applications or research does it open?
6. CRITICAL APPRAISAL (4-6 sentences): Judge the quality of the evidence. Explicitly cover: design strength (RCT, cohort, case series, review…), the main risks of bias (selection, performance, attrition, funding/conflict of interest), statistical robustness (sample size, confidence intervals, multiplicity), and the applicability/generalizability of the findings.
7. EVIDENCE LEVEL & VERDICT (1-2 sentences): State an overall certainty (High / Moderate / Low / Very low) and a one-line verdict on how trustworthy and useful the study is.

Rules:
- In English, academic but accessible tone
- 300-550 words
- Cite specific numbers and data when available
- The CRITICAL APPRAISAL and VERDICT are mandatory, even if brief
- If the text is only an abstract (not full text), add [based on abstract only] and stay cautious in the appraisal
- Do not speculate beyond what the text says"""


def deep_research(query: str, max_articles: int = 3, lang: str = "fr",
                  sources: List[str] = None, allow_scihub: Optional[bool] = None,
                  progress_cb=None) -> List[Dict]:
    """Run deep research pipeline.

    Returns list of article dicts with enriched 'deep_summary' field.
    ``allow_scihub`` is the Sci-Hub fallback for paywalled studies. It is the
    always-on default (``None`` → resolved on at the download layer); pass
    ``False`` only to force it off. It is independent of the search depth — the
    depth knob governs only the per-article AI synthesis.
    ``progress_cb(stage, detail, current, total)`` — when supplied — is called as
    the pipeline advances so the UI can paint a live staged progress panel. It
    must be cheap and thread-safe (the TUI passes a ProgressChannel.report).
    """
    def _emit(stage, detail="", current=0, total=0):
        if progress_cb:
            try:
                progress_cb(stage, detail, current, total)
            except Exception:
                pass  # progress is best-effort; never break the pipeline for it

    if sources is None:
        sources = list(config.DEFAULT_DEEP_SOURCES)

    print(f"\n🔬 DEEP RESEARCH — \"{query}\"")
    print(f"   Max articles: {max_articles} | Lang: {lang}")
    print("=" * 60)

    # Step 1: Collect articles with DOIs from all sources
    print("\n[1/4] Collecte des articles depuis les sources...")
    all_articles = []

    source_results = {}
    if "pubmed" in sources:
        print("   → PubMed...")
        _emit("sources", "PubMed")
        r = search_pubmed(query, max_results=max_articles)
        source_results["pubmed"] = r
        all_articles.extend(r)

    if "crossref" in sources:
        print("   → CrossRef...")
        _emit("sources", "CrossRef")
        r = search_crossref(query, max_results=max_articles)
        source_results["crossref"] = r
        all_articles.extend(r)

    if "openalex" in sources:
        print("   → OpenAlex...")
        _emit("sources", "OpenAlex")
        r = search_openalex(query, max_results=max_articles)
        source_results["openalex"] = r
        all_articles.extend(r)

    if "europe_pmc" in sources:
        print("   → Europe PMC...")
        _emit("sources", "Europe PMC")
        r = search_europe_pmc(query, max_results=max_articles)
        source_results["europe_pmc"] = r
        all_articles.extend(r)

    if "biorxiv" in sources:
        print("   → bioRxiv...")
        _emit("sources", "bioRxiv")
        r = search_biorxiv(query, max_results=max_articles)
        source_results["biorxiv"] = r
        all_articles.extend(r)

    if "medrxiv" in sources:
        print("   → medRxiv...")
        _emit("sources", "medRxiv")
        r = search_medrxiv(query, max_results=max_articles)
        source_results["medrxiv"] = r
        all_articles.extend(r)

    # Deduplicate by DOI (and by title for DOI-less articles)
    seen_dois = set()
    seen_titles = set()
    unique_articles = []
    for a in all_articles:
        if a.get("error"):
            continue
        doi = a.get("doi", "")
        if doi and doi not in seen_dois:
            seen_dois.add(doi)
            unique_articles.append(a)
        elif not doi:  # keep articles without DOI too — O(1) title lookup
            title = a.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_articles.append(a)

    # Limit to max_articles
    unique_articles = unique_articles[:max_articles]
    _emit("dedup", f"{len(unique_articles)} unique", len(unique_articles), len(unique_articles))
    print(f"   → {len(unique_articles)} article(s) unique(s) avec DOI")

    if not unique_articles:
        print("   ⚠️ Aucun article trouvé. Essayez une autre requête.")
        return []

    # Step 2: Download PDFs and generate deep summaries
    print(f"\n[2/4] Téléchargement PDFs + Deep Summaries...")
    results = []

    n_unique = len(unique_articles)
    for i, article in enumerate(unique_articles, 1):
        doi = article.get("doi", "")
        title = article.get("title", "Unknown")[:80]
        _emit("process", f"PDF · {title[:42]}", i, n_unique)
        print(f"\n   [{i}/{n_unique}] {title}...")
        print(f"       DOI: {doi or 'N/A'}")

        deep_summary = ""
        pdf_path = ""
        summary_method = ""

        known_urls = [u for u in (article.get("oa_pdf_url"), article.get("pdf_url"), article.get("url")) if u]
        if doi or known_urls:
            # Try PDF download (known URLs first, then OA, then Sci-Hub if allowed)
            print(f"       Téléchargement PDF...")
            try:
                from pdf_sender import download_pdf as _dl
                dl_result = _dl(doi, title=article.get("title") or "",
                                known_urls=known_urls, allow_scihub=allow_scihub)
            except Exception:
                # The Sci-Hub fallback is DOI-keyed; a URL-only article (doi falsy)
                # would crash on None.replace(...) inside try_download_pdf, so skip it.
                dl_result = (try_download_pdf(doi, allow_scihub=allow_scihub) if doi
                             else {"success": False, "error": "no DOI for Sci-Hub fallback"})
            if dl_result["success"]:
                pdf_path = dl_result["path"]
                method = dl_result["method"]
                print(f"       ✓ PDF récupéré ({method}) — parsing...")
                pdf_text = parse_pdf_for_summary(pdf_path)
                if pdf_text and len(pdf_text) > 200:
                    print(f"       ✓ PDF text extracted ({len(pdf_text)} chars)")
                    # Generate deep summary from full text
                    _emit("process", f"AI synthesis · {title[:38]}", i, n_unique)
                    deep_summary = _summarize_full_text(pdf_text, title, lang)
                    summary_method = f"full_text_{method}"
                else:
                    print(f"       ⚠ PDF parse failed — utilisant l'abstract")
                    deep_summary = _summarize_abstract(article, lang, pdf_available=False)
                    summary_method = "abstract"
            else:
                dl_error = dl_result.get("error", "download failed")
                print(f"       ⚠ PDF non disponible — utilisant l'abstract ({dl_error})")
                deep_summary = _summarize_abstract(article, lang, pdf_available=False)
                summary_method = "abstract"
        else:
            print(f"       ⚠ No DOI — utilisant l'abstract")
            deep_summary = _summarize_abstract(article, lang, pdf_available=False)
            summary_method = "abstract"

        article["deep_summary"] = deep_summary
        article["summary_method"] = summary_method
        article["pdf_available"] = bool(pdf_path)
        results.append(article)

        # Rate limit between MiniMax calls
        time.sleep(2)

    print(f"\n[3/4] Résumés générés: {len(results)}")
    print(f"[4/4] Terminé.\n")
    print("=" * 60)

    return results


def _summarize_full_text(pdf_text: str, title: str, lang: str) -> str:
    """Send full PDF text to MiniMax for deep summary."""
    # Truncate text to fit token budget (use first 30k chars = ~7500 tokens)
    truncated = pdf_text[:30000] if len(pdf_text) > 30000 else pdf_text

    if lang == "fr":
        system = DEEP_SYSTEM_PROMPT
        user = f"TITRE: {title}\n\nTEXTE COMPLET:\n{truncated}"
    else:
        system = ENGLISH_SYSTEM_PROMPT
        user = f"TITLE: {title}\n\nFULL TEXT:\n{truncated}"

    return _call_minimax_deep(system, user)


def _summarize_abstract(article: Dict, lang: str, pdf_available: bool = True) -> str:
    """Generate deep summary from abstract only (fallback).

    Args:
        pdf_available: if False, the notice [PDF non disponible] is prepended.
    """
    title = article.get("title", "Unknown")
    abstract = article.get("abstract", "")
    authors = article.get("authors", [])
    date = article.get("date", "")

    if lang == "fr":
        notice = "⚠️ *Étude non disponible en PDF — résumé basé sur l'abstract uniquement.*\n\n" if not pdf_available else ""
    else:
        notice = "⚠️ *Study not available as PDF — summary based on the abstract only.*\n\n" if not pdf_available else ""

    if not abstract:
        return f"{notice}[Résumé complet non disponible — abstract vide]" if lang == "fr" else f"{notice}[Full summary unavailable — empty abstract]"

    if lang == "fr":
        system = DEEP_SYSTEM_PROMPT
        user = (f"TITRE: {title}\n"
                f"AUTEURS: {', '.join(authors[:3])}\n"
                f"DATE: {date}\n"
                f"SOURCE: {article.get('source', 'Unknown')}\n\n"
                f"TEXTE:\n{notice}{abstract}")
    else:
        system = ENGLISH_SYSTEM_PROMPT
        user = (f"TITLE: {title}\n"
                f"AUTHORS: {', '.join(authors[:3])}\n"
                f"DATE: {date}\n"
                f"SOURCE: {article.get('source', 'Unknown')}\n\n"
                f"TEXT:\n{notice}{abstract}")

    return _call_minimax_deep(system, user)


def _call_minimax_deep(system: str, user: str) -> str:
    """Call MiniMax for a deep summary via the unified client.

    The unified client disables the `<think>` trace (so the whole token budget
    no longer gets spent on reasoning and returned empty), strips any stray
    wrapper, surfaces HTTP/`base_resp` errors, and retries transient failures.
    """
    import minimax_client
    # Bumped from 1500 → 2400 so the richer 7-section synthesis (now including a
    # mandatory critical appraisal + verdict) is not truncated mid-evaluation.
    return minimax_client.chat(user, system=system, max_tokens=2400,
                               temperature=0.3, timeout=120)


def format_deep_results(results: List[Dict], query: str, lang: str = "fr") -> str:
    """Format deep research results as a readable text output."""
    if not results:
        return "Aucun résultat."

    lines = []
    lines.append(f"🔬 Deep Research — \"{query}\"")
    lines.append(f"   {len(results)} article(s) analysé(s)\n")
    lines.append("=" * 60)

    for i, r in enumerate(results, 1):
        title = r.get("title", "Unknown")[:85]
        source = r.get("source", "")
        date = r.get("date", "")
        doi = r.get("doi", "")
        method = r.get("summary_method", "")
        authors = r.get("authors", [])[:3]
        journal = r.get("journal", "")
        pdf_avail = r.get("pdf_available", False)
        pdf_icon = "📄" if pdf_avail else "🔒"

        lines.append(f"\n{i}. {title}")
        lines.append(f"   📖 {source}" + (f" | 📅 {date}" if date else "") +
                    (f" | 🏛 {journal}" if journal else ""))
        lines.append(f"   👥 {', '.join(authors)}" if authors else "")
        lines.append(f"   🔑 DOI: {doi}" if doi else "")
        lines.append(f"   {pdf_icon} {'PDF disponible' if pdf_avail else 'PDF non disponible'} | 📊 {method}")

        deep = r.get("deep_summary", "")
        if deep:
            lines.append(f"\n   {deep}")

        lines.append("-" * 60)

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Deep Research — PDF + full-text summaries")
    parser.add_argument("query", help="Recherche")
    parser.add_argument("--max", type=int, default=3, help="Nb articles (défaut: 3)")
    parser.add_argument("--lang", default="fr", choices=["fr", "en"], help="Langue")
    parser.add_argument("--sources", default="pubmed,crossref,openalex,europe_pmc,biorxiv,medrxiv",
                        help="Sources (défaut: all)")
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    results = deep_research(args.query, max_articles=args.max,
                          lang=args.lang, sources=sources)

    output = format_deep_results(results, args.query, lang=args.lang)
    print(output)
