#!/usr/bin/env python3
"""Bloomberg-style research terminal / Research Desk for Dr_NewPaper."""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).parent))

from config import DEFAULT_DB_PATH, DEFAULT_LANG, DEFAULT_MAX_RESULTS, DEFAULT_SOURCES  # noqa: E402
from dispatcher import ALL_SOURCES, RechercheDispatcher  # noqa: E402
import deep_research  # noqa: E402
import meta_analysis as meta_mod  # noqa: E402
import research_exports  # noqa: E402
import scoring  # noqa: E402
from storage import ResearchStore  # noqa: E402

RESET = "\033[0m"; CYAN = "\033[36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; MAGENTA = "\033[35m"; BLUE = "\033[34m"


@dataclass(frozen=True)
class ResearchArticle:
    title: str
    source: str = "unknown"
    date: str = ""
    authors: list[str] = field(default_factory=list)
    doi: str = ""
    journal: str = ""
    summary: str = ""
    deep_summary: str = ""
    abstract: str = ""
    url: str = ""
    oa_pdf_url: str = ""
    pdf_url: str = ""
    summary_method: str = ""
    type: str = ""
    pmid: str = ""
    citation_count: int = 0
    oa_status: str = ""

    @classmethod
    def from_raw(cls, raw: dict) -> "ResearchArticle":
        """Build the in-memory article from any source/normalized/DB-row dict.

        Tolerates the field-name drift across layers (``date`` vs
        ``publication_date``, ``authors`` vs ``authors_json``) so no metadata is
        dropped on the way into the dataclass — the single coercion point.
        """
        def _int(v):
            try:
                return max(0, int(float(v)))
            except (TypeError, ValueError):
                return 0
        return cls(
            title=str(raw.get("title") or "Untitled").strip(),
            source=str(raw.get("source") or "unknown"),
            date=str(raw.get("date") or raw.get("publication_date") or ""),
            authors=_coerce_authors(raw.get("authors") or raw.get("authors_json")),
            doi=str(raw.get("doi") or ""), journal=str(raw.get("journal") or ""),
            summary=str(raw.get("summary") or ""), deep_summary=str(raw.get("deep_summary") or ""),
            abstract=str(raw.get("abstract") or ""), url=str(raw.get("url") or ""),
            oa_pdf_url=str(raw.get("oa_pdf_url") or ""), pdf_url=str(raw.get("pdf_url") or ""),
            summary_method=str(raw.get("summary_method") or ""),
            type=str(raw.get("type") or raw.get("article_type") or ""),
            pmid=str(raw.get("pmid") or ""),
            citation_count=_int(raw.get("citation_count")),
            oa_status=str(raw.get("oa_status") or ""),
        )

    def to_payload(self) -> dict:
        """Plain dict for storage/normalization (carries every field, no drops)."""
        return dict(asdict(self))

    @property
    def has_pdf(self) -> bool:
        return bool(self.oa_pdf_url or self.pdf_url)

    @property
    def best_summary(self) -> str:
        for candidate in (self.deep_summary, self.summary, self.abstract):
            if candidate and not _is_model_error(candidate):
                return candidate
        return "Résumé automatique indisponible pour cet article."


def _is_model_error(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith("[") and any(marker in normalized for marker in ("minimax error", "erreur minimax"))


def _coerce_authors(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v]
        except Exception:
            pass
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def normalize_articles(raw_articles: Iterable[dict]) -> list[ResearchArticle]:
    articles: list[ResearchArticle] = []
    for raw in raw_articles or []:
        if not isinstance(raw, dict) or raw.get("error"):
            continue
        articles.append(ResearchArticle.from_raw(raw))
    return articles


def build_scorecard(articles: Sequence[ResearchArticle]) -> dict:
    by_source = Counter(a.source for a in articles if a.source)
    return {"articles": len(articles), "sources": dict(by_source), "with_doi": sum(1 for a in articles if a.doi), "with_pdf": sum(1 for a in articles if a.has_pdf)}


def format_sources_bar(selected: Sequence[str], all_sources: Sequence[str] = ALL_SOURCES) -> str:
    selected_set = set(selected or [])
    return "  ".join(f"[{'x' if s in selected_set else ' '}] {s}" for s in all_sources)


def _ansi(text: str, color: str, enabled: bool = True) -> str:
    return f"{color}{text}{RESET}" if enabled else text


def _box(title: str, body: str, width: int = 112, color: str = CYAN, ansi: bool = True) -> str:
    title_text = f" {title} "
    top = "┌" + title_text + "─" * max(0, width - len(title_text) - 2) + "┐"
    lines = [top]
    for raw_line in body.splitlines() or [""]:
        for line in textwrap.wrap(raw_line, width=width - 4, replace_whitespace=False) or [""]:
            lines.append("│ " + line.ljust(width - 4) + " │")
    lines.append("└" + "─" * (width - 2) + "┘")
    return _ansi("\n".join(lines), color, ansi)


def render_dashboard(query: str, articles: Sequence[ResearchArticle], selected_sources: Sequence[str], mode: str = "search", lang: str = "fr", ansi: bool = True) -> str:
    score = build_scorecard(articles)
    source_counts = "  ".join(f"{src}:{count}" for src, count in score["sources"].items()) or "aucune"
    header = f"DR_NEWPAPER TERMINAL  |  MODE {mode.upper()}  |  LANG {lang.upper()}\nQUERY: {query}\nARTICLES {score['articles']}  DOI {score['with_doi']}  PDF {score['with_pdf']}  SOURCES {source_counts}"
    parts = [_box("RESEARCH COMMAND CENTER", header, color=GREEN, ansi=ansi), _box("SOURCE MATRIX", format_sources_bar(selected_sources, ALL_SOURCES), color=BLUE, ansi=ansi)]
    if not articles:
        parts.append(_box("RESULTS", "Aucun résultat.", color=RED, ansi=ansi)); return "\n".join(parts)
    rows = []
    for idx, article in enumerate(articles, 1):
        rows.append(f"{idx:02d} {'PDF' if article.has_pdf else 'LOCK':<4} {article.source:<16} {article.date or 'n.d.':<10} {(article.doi or 'no-doi'):<24} {article.title[:92]}")
    parts.append(_box("ARTICLE TAPE", "\n".join(rows), color=CYAN, ansi=ansi))
    focus = []
    for idx, article in enumerate(articles[:5], 1):
        authors = ", ".join(article.authors[:3]) or "auteurs inconnus"
        summary = textwrap.shorten(article.best_summary.replace("\n", " "), width=430, placeholder="…")
        focus.append(f"#{idx} {article.title}\n    {article.source} | {article.date or 'n.d.'} | {authors}\n    {summary}\n    DOI: {article.doi or '—'} | URL: {article.url or article.oa_pdf_url or article.pdf_url or '—'}")
    parts.append(_box("ANALYST BRIEF", "\n\n".join(focus), color=YELLOW, ansi=ansi))
    return "\n".join(parts)


def parse_sources(raw: str) -> list[str]:
    if raw == "all":
        return list(ALL_SOURCES)
    sources = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = [s for s in sources if s not in ALL_SOURCES]
    if unknown:
        raise ValueError(f"Sources inconnues: {', '.join(unknown)}")
    return sources


def run_search(query: str, max_results: int, lang: str, sources: Sequence[str], deep: bool = False,
               allow_scihub: Optional[bool] = None, progress_cb=None) -> list[ResearchArticle]:
    if deep:
        raw = deep_research.deep_research(query, max_articles=max_results, lang=lang,
                                          sources=list(sources), allow_scihub=allow_scihub,
                                          progress_cb=progress_cb)
    else:
        if progress_cb:
            progress_cb("sources", ", ".join(sources), 0, 0)
        raw = RechercheDispatcher(output_mode="file", lang=lang).search(query, sources=list(sources), max_results=max_results)
    return normalize_articles(raw)


def _save_articles(store: ResearchStore, articles: Sequence[ResearchArticle], query: str) -> list[int]:
    ids = []
    for article in articles:
        payload = asdict(article)
        aid = store.upsert_article(payload, query=query)
        row = store.get_article(aid) or payload
        store.upsert_score(aid, scoring.score_article(row))
        ids.append(aid)
    return ids


def _store_articles_as_research_articles(rows: list[dict]) -> list[ResearchArticle]:
    return normalize_articles(rows)


def render_tape(rows: list[dict], ansi: bool = True) -> str:
    if not rows:
        return _box("ARTICLE TAPE", "Aucun article stocké.", color=RED, ansi=ansi)
    lines = []
    for r in rows:
        lines.append(f"{r.get('label') or 'WATCH':<5} {r.get('final_score') or 0:>3}  #{r.get('id'):<4} {(r.get('source') or ''):<12} {(r.get('publication_date') or '')[:10]:<10} {r.get('title','')[:82]}")
    return _box("ARTICLE TAPE", "\n".join(lines), color=CYAN, ansi=ansi)


def render_article(row: dict | None, ansi: bool = True) -> str:
    if not row:
        return _box("ARTICLE DETAIL", "Article introuvable.", color=RED, ansi=ansi)
    authors = ", ".join(_coerce_authors(row.get("authors_json"))) or "—"
    body = f"ID: {row.get('id')}\nLABEL/SCORE: {row.get('label') or 'WATCH'} {row.get('final_score') or 0}\nTITLE: {row.get('title')}\nAUTHORS: {authors}\nJOURNAL: {row.get('journal') or '—'}\nDATE: {row.get('publication_date') or '—'}\nDOI: {row.get('doi') or '—'}\nURL: {row.get('url') or '—'}\nPDF: {row.get('oa_pdf_url') or row.get('pdf_url') or '—'}\n\nABSTRACT:\n{row.get('abstract') or '—'}"
    return _box("ARTICLE DETAIL", body, color=YELLOW, ansi=ansi)


def render_home(store: ResearchStore, ansi: bool = True) -> str:
    watchlists = store.list_watchlists()
    rows = store.list_articles(limit=8)
    body = f"DB: {store.db_path}\nWatchlists: {len(watchlists)}\nArticles suivis: {len(store.list_articles(limit=100000))}\n\nTop signals:\n"
    body += "\n".join(f"- {r.get('label') or 'WATCH'} {r.get('final_score') or 0}: {r.get('title','')[:90]}" for r in rows) or "- Aucun article."
    return _box("DR_NEWPAPER HOME", body, color=GREEN, ansi=ansi)


def render_meta(query: str, max_articles: int, lang: str, deep: bool, ansi: bool = True) -> str:
    result = meta_mod.perform_meta_analysis(query, max_articles=max_articles, deep=deep, lang=lang)
    articles = normalize_articles(result.get("articles", []))
    dashboard = render_dashboard(query, articles, ["crossref", "openalex", "pubmed", "europe_pmc"], "meta", lang, ansi)
    return dashboard + "\n" + _box("META-ANALYSIS", result.get("summary") or "Analyse indisponible.", color=MAGENTA, ansi=ansi)


def _legacy_or_search_argv(argv: Sequence[str]) -> list[str]:
    if not argv:
        return list(argv)
    commands = {"home", "search", "tape", "article", "watch", "digest", "compare"}
    if argv[0] in commands or argv[0].startswith("-"):
        return list(argv)
    return ["search", *argv]


def _extract_global_options(argv: list[str]) -> tuple[list[str], str, bool]:
    db = str(DEFAULT_DB_PATH)
    no_color = False
    clean: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--no-color":
            no_color = True; i += 1; continue
        if argv[i] == "--db" and i + 1 < len(argv):
            db = argv[i + 1]; i += 2; continue
        clean.append(argv[i]); i += 1
    return clean, db, no_color


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv, db_override, no_color_override = _extract_global_options(list(argv or sys.argv[1:]))
    argv = _legacy_or_search_argv(raw_argv)
    parser = argparse.ArgumentParser(description="Dr_NewPaper Research Desk / Bloomberg terminal")
    parser.add_argument("--db", default=db_override, help="SQLite DB path")
    parser.add_argument("--no-color", action="store_true", default=no_color_override)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("home"); p.set_defaults(cmd="home")
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS); p.add_argument("--lang", choices=["fr","en","ar"], default=DEFAULT_LANG); p.add_argument("--sources", default=",".join(DEFAULT_SOURCES)); p.add_argument("--deep", action="store_true"); p.add_argument("--meta", action="store_true"); p.add_argument("--save", action="store_true")
    p = sub.add_parser("tape"); p.add_argument("--query", default=""); p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("article"); p.add_argument("identifier")
    p = sub.add_parser("watch"); watch_sub = p.add_subparsers(dest="watch_cmd", required=True)
    wa = watch_sub.add_parser("add"); wa.add_argument("name"); wa.add_argument("query"); wa.add_argument("--sources", default=",".join(DEFAULT_SOURCES)); wa.add_argument("--lang", default=DEFAULT_LANG)
    watch_sub.add_parser("list")
    wr = watch_sub.add_parser("run"); wr.add_argument("name"); wr.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS)
    p = sub.add_parser("digest"); p.add_argument("query", nargs="?", default=""); p.add_argument("--since", default="30d"); p.add_argument("--limit", type=int, default=50); p.add_argument("--output-dir", default="")
    p = sub.add_parser("compare"); p.add_argument("identifiers", nargs="+")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code)
    ansi = not args.no_color and os.getenv("NO_COLOR") is None
    store = ResearchStore(args.db)
    try:
        if args.cmd == "home":
            print(render_home(store, ansi)); return 0
        if args.cmd == "search":
            sources = parse_sources(args.sources)
            if args.meta:
                print(render_meta(args.query, args.max, args.lang, args.deep, ansi)); return 0
            articles = run_search(args.query, args.max, args.lang, sources, args.deep)
            if args.save:
                _save_articles(store, articles, args.query)
            print(render_dashboard(args.query, articles, sources, "deep" if args.deep else "search", args.lang, ansi)); return 0
        if args.cmd == "tape":
            print(render_tape(store.list_articles(limit=args.limit, query=args.query), ansi)); return 0
        if args.cmd == "article":
            print(render_article(store.get_article(args.identifier), ansi)); return 0
        if args.cmd == "watch":
            if args.watch_cmd == "add":
                wid = store.add_watchlist(args.name, args.query, parse_sources(args.sources), args.lang)
                print(f"Watchlist ajoutée: {args.name} (id={wid})"); return 0
            if args.watch_cmd == "list":
                rows = store.list_watchlists(); print(_box("WATCHLISTS", "\n".join(f"#{r['id']} {r['name']} :: {r['query']} :: {json.loads(r['sources_json'])}" for r in rows) or "Aucune watchlist.", color=BLUE, ansi=ansi)); return 0
            if args.watch_cmd == "run":
                w = store.get_watchlist(args.name)
                if not w: print(f"Watchlist introuvable: {args.name}"); return 1
                sources = json.loads(w["sources_json"]); articles = run_search(w["query"], args.max, w["lang"], sources, False); ids = _save_articles(store, articles, w["query"]); new_ids = store.record_watchlist_hits(w["id"], ids)
                print(render_dashboard(w["query"], articles, sources, "watch", w["lang"], ansi)); print(f"Nouveaux articles: {len(new_ids)} / {len(ids)}"); return 0
        if args.cmd == "digest":
            rows = store.list_articles(limit=args.limit, query=args.query)
            out_dir = Path(args.output_dir) if args.output_dir else Path("Dossier") / (args.query or "research_dossier").replace(" ", "_")
            written = research_exports.export_research_dossier(out_dir, args.query or "Research Desk", rows, args.since)
            print("Dossier exporté:"); [print(f"- {k}: {v}") for k, v in written.items()]; return 0
        if args.cmd == "compare":
            rows = [store.get_article(x) for x in args.identifiers]; rows = [r for r in rows if r]
            body = "Article | Score | Evidence | Risk | DOI\n--- | ---: | ---: | ---: | ---\n" + "\n".join(f"{r.get('title','')[:55]} | {r.get('final_score') or 0} | {r.get('evidence_score') or 0} | {r.get('risk_score') or 0} | {r.get('doi') or '—'}" for r in rows)
            print(_box("COMPARE", body, color=MAGENTA, ansi=ansi)); return 0
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
