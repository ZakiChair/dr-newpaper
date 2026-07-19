"""Dispatcher — orchestrates search sources + summarization + output."""

from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from sources.arxiv import search_arxiv
from sources.pubmed import search_pubmed
from sources.scholar import search_scholar
from sources.scihub import search_scihub
from sources.crossref import search_crossref
from sources.openalex import search_openalex
from sources.semantic_scholar import search_semantic_scholar
from sources.core_ac import search_core
from sources.europe_pmc import search_europe_pmc
from sources.unpaywall import enrich_article_with_oa
from sources.biorxiv import search_biorxiv, search_medrxiv
from sources.base import search_base, search_base_all
from sources.eric import search_eric
from sources.jstor import search_jstor
from sources.clinical_trials import search_trials
from minimax_client import summarize_batch
from file_writer import write_results

ALL_SOURCES = [
    "arxiv", "pubmed", "scholar", "crossref", "openalex",
    "semantic_scholar", "core", "europe_pmc", "biorxiv", "medrxiv",
    "base", "eric", "jstor", "clinical_trials",
]

# Tiered sources — primary searched first, secondary only if needed
PRIMARY_SOURCES = ["pubmed", "openalex", "crossref", "semantic_scholar", "core"]
SECONDARY_SOURCES = ["europe_pmc", "arxiv", "biorxiv", "medrxiv", "base", "eric", "jstor", "clinical_trials", "scholar"]

# Source function map
_SOURCE_FUNCTIONS = {
    "arxiv": search_arxiv,
    "pubmed": search_pubmed,
    "scholar": search_scholar,
    "crossref": search_crossref,
    "openalex": search_openalex,
    "semantic_scholar": search_semantic_scholar,
    "core": search_core,
    "europe_pmc": search_europe_pmc,
    "biorxiv": search_biorxiv,
    "medrxiv": search_medrxiv,
    "base": search_base,
    "eric": search_eric,
    "jstor": search_jstor,
    "clinical_trials": search_trials,
}

def _safe_article(article: Dict) -> Dict:
    """Ensure article has all required fields, never returns None."""
    return {
        "source": article.get("source") or "unknown",
        "title": article.get("title") or "Untitled",
        "abstract": article.get("abstract") or "",
        "date": article.get("date") or "",
        "url": article.get("url") or "",
        "authors": article.get("authors") or [],
        "doi": article.get("doi") or "",
        "journal": article.get("journal") or "",
        "type": article.get("type") or "",
        "summary": article.get("summary") or "",
        "deep_summary": article.get("deep_summary") or "",
        "summary_method": article.get("summary_method") or "",
        "error": article.get("error") or "",
        "pmid": article.get("pmid") or "",
        "oa_pdf_url": article.get("oa_pdf_url") or "",
        "pdf_url": article.get("pdf_url") or "",
        # Metadata the whitelist used to silently drop: citations feed the score,
        # OA status feeds "free full text available" signals downstream.
        "citation_count": article.get("citation_count") or 0,
        "oa_status": article.get("oa_status") or "",
    }


class RechercheDispatcher:
    def __init__(self, output_mode: str = "file", lang: str = "en"):
        self.output_mode = output_mode
        self.lang = lang

    def search(
        self,
        query: str,
        sources: List[str] | None = None,
        max_results: int = 5,
        min_results: int = 5,
    ) -> List[Dict]:
        """
        Run search across all/specified sources, summarize, and dispatch.

        Args:
            query: search term
            sources: list of source names (default: all)
            max_results: max per source
            min_results: if primary sources return fewer than this, secondary
                         sources are queried. Default 5.
        """
        # Determine which sources to run
        if sources is not None:
            # Explicit sources passed (e.g. from /deep) — run all, no tiering
            active_sources = list(sources)
        elif min_results <= 0:
            # min_results=0 means "primary only, no fallback"
            active_sources = PRIMARY_SOURCES.copy()
        else:
            # Tiered: start with primary, expand if needed
            active_sources = PRIMARY_SOURCES.copy()

        # Remove arxiv if blocked (known issue on this server)
        if "arxiv" in active_sources:
            try:
                import urllib.request
                with urllib.request.urlopen(
                    "https://export.arxiv.org/api/query?search_query=all:test&max_results=1",
                    timeout=5,
                ) as resp:
                    pass
            except Exception:
                print("   ⚠️ arXiv bloqué depuis ce serveur — retiré des sources")
                active_sources = [s for s in active_sources if s != "arxiv"]
                SECONDARY_SOURCES_CLEAN = [s for s in SECONDARY_SOURCES if s != "arxiv"]
            else:
                SECONDARY_SOURCES_CLEAN = SECONDARY_SOURCES
        else:
            SECONDARY_SOURCES_CLEAN = SECONDARY_SOURCES

        all_results = []

        # ── Phase 1: primary sources in parallel ──────────────────────────
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_SOURCE_FUNCTIONS[s], query, max_results): s
                for s in active_sources
                if s in _SOURCE_FUNCTIONS
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    res = future.result()
                    if res:
                        all_results.extend(res)
                        print(f"   ✅ {source}: {len(res)} résultats")
                except Exception as e:
                    print(f"   ⚠️ {source} error: {e}")

        # ── Phase 2: secondary sources if not enough results ─────────────────
        if sources is None and min_results > 0 and len(all_results) < min_results:
            missing = min_results - len(all_results)
            print(f"   🔍 {len(all_results)} résultats — {missing} de plus requis → sources secondaires")
            secondary = [s for s in SECONDARY_SOURCES_CLEAN if s not in active_sources]
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {
                    pool.submit(_SOURCE_FUNCTIONS[s], query, max_results): s
                    for s in secondary
                    if s in _SOURCE_FUNCTIONS
                }
                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        res = future.result()
                        if res:
                            all_results.extend(res)
                            print(f"   ✅ {source}: {len(res)} résultats")
                    except Exception as e:
                        print(f"   ⚠️ {source} error: {e}")

        # Deduplicate by title (simple)
        seen = set()
        unique_results = []
        for r in all_results:
            if not isinstance(r, dict):
                continue
            title = r.get("title") or ""
            if title and title not in seen and not r.get("error"):
                seen.add(title)
                unique_results.append(_safe_article(r))

        # Summarize with MiniMax — each article is guaranteed to be a dict
        summarized = summarize_batch(unique_results, lang=self.lang)

        # Dispatch
        if self.output_mode in ["file", "both"]:
            written = write_results(summarized, query)
            print(f"📁 {len(written)} fichier(s) écrit(s)")

        return summarized