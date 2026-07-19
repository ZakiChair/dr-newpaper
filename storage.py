"""SQLite storage layer for Dr_NewPaper Research Desk."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Mapping, Any

from normalization import canonical_article_key, merge_article_records, normalize_article, now_iso
from config import DEFAULT_DB_PATH


def _as_int(value: Any) -> int:
    """Coerce a citation count (str/float/None) to a non-negative int."""
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


# Score columns from the scores-table join: these are authoritative and never
# live inside an article's raw_json, so they overlay last during hydration.
_SCORE_FIELDS = (
    "final_score", "evidence_score", "novelty_score", "citation_score",
    "clinical_relevance_score", "risk_score", "label", "explanation_json",
)


def _hydrate_row(row: "sqlite3.Row | Mapping[str, Any]") -> dict:
    """Re-expand a column-projection row back into the full Article object.

    The articles table stores only the indexed/normalized columns; everything
    else (citation_count nuance, oa_status, license, deep_summary, summary_method,
    publication type detail) is preserved inside ``raw_json``. Readers that used
    ``get_article``/``list_articles`` previously saw a thin projection and so
    silently lost those fields (the cause of citation_score always being 0 and
    study-type being invisible). This rebuilds the rich object once, centrally.

    Precedence: raw_json (base) → non-empty article columns → score-join fields
    (always last). Empty/zero columns never shadow a richer raw_json value, so a
    freshly ALTERed ``citation_count`` default of 0 cannot clobber a real count.
    Also re-exposes the back-compat aliases readers expect: ``type`` (from
    ``article_type``) and ``date`` (from ``publication_date``).
    """
    d = dict(row)
    try:
        base = json.loads(d.get("raw_json") or "{}")
    except Exception:
        base = {}
    merged: dict = dict(base) if isinstance(base, dict) else {}
    for key, value in d.items():
        if key in _SCORE_FIELDS:
            continue  # handled last
        if key in merged and (value is None or value == "" or value == 0):
            continue  # keep the richer raw_json value
        merged[key] = value
    for key in _SCORE_FIELDS:
        if key in d:
            merged[key] = d[key]
    merged["type"] = merged.get("type") or d.get("article_type") or ""
    merged["date"] = merged.get("date") or d.get("publication_date") or ""
    merged["citation_count"] = _as_int(merged.get("citation_count"))
    return merged


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT UNIQUE NOT NULL,
    doi TEXT,
    pmid TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    authors_json TEXT NOT NULL DEFAULT '[]',
    journal TEXT,
    publication_date TEXT,
    year TEXT,
    source TEXT,
    sources_json TEXT NOT NULL DEFAULT '[]',
    url TEXT,
    oa_pdf_url TEXT,
    pdf_url TEXT,
    article_type TEXT,
    citation_count INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_doi ON articles(doi);
CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(publication_date);
CREATE INDEX IF NOT EXISTS idx_articles_score_date ON articles(last_seen_at);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    model TEXT,
    lang TEXT,
    summary_short TEXT,
    summary_structured_json TEXT,
    raw_text TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS scores (
    article_id INTEGER PRIMARY KEY,
    novelty_score INTEGER NOT NULL,
    evidence_score INTEGER NOT NULL,
    citation_score INTEGER NOT NULL,
    clinical_relevance_score INTEGER NOT NULL,
    risk_score INTEGER NOT NULL,
    final_score INTEGER NOT NULL,
    label TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS watchlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    query TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    lang TEXT NOT NULL DEFAULT 'fr',
    cadence TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS watchlist_hits (
    watchlist_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    seen_at TEXT NOT NULL,
    first_seen INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(watchlist_id, article_id),
    FOREIGN KEY(watchlist_id) REFERENCES watchlists(id),
    FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS pdfs (
    article_id INTEGER PRIMARY KEY,
    pdf_url TEXT,
    local_path TEXT,
    extraction_status TEXT,
    full_text_path TEXT,
    checksum TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE TABLE IF NOT EXISTS meta_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'fr',
    n_studies INTEGER NOT NULL DEFAULT 0,
    depth TEXT,
    document_md TEXT NOT NULL,
    md_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meta_created ON meta_analyses(created_at);
"""


class ResearchStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Additive column migrations for databases created before a column existed.

        SQLite can only ADD COLUMN, which is all we need. citation_count was
        added after the first schema; existing rows backfill from raw_json so the
        value isn't lost for already-stored articles.
        """
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(articles)")}
        if "citation_count" not in cols:
            self.conn.execute("ALTER TABLE articles ADD COLUMN citation_count INTEGER NOT NULL DEFAULT 0")
            self.conn.execute(
                "UPDATE articles SET citation_count = "
                "CAST(COALESCE(json_extract(raw_json, '$.citation_count'), 0) AS INTEGER)"
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ResearchStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def upsert_article(self, article: Mapping[str, Any], query: str = "") -> int:
        normalized = normalize_article(article)
        canonical = normalized["canonical_id"]
        now = now_iso()
        existing = self.conn.execute("SELECT * FROM articles WHERE canonical_id=?", (canonical,)).fetchone()
        if existing:
            current_raw = json.loads(existing["raw_json"] or "{}")
            merged = merge_article_records(current_raw, normalized)
            self.conn.execute(
                """
                UPDATE articles SET doi=?, pmid=?, title=?, abstract=?, authors_json=?, journal=?,
                    publication_date=?, year=?, source=?, sources_json=?, url=?, oa_pdf_url=?, pdf_url=?,
                    article_type=?, citation_count=?, raw_json=?, last_seen_at=? WHERE id=?
                """,
                self._article_values(merged) + (now, existing["id"]),
            )
            self.conn.commit()
            return int(existing["id"])

        self.conn.execute(
            """
            INSERT INTO articles(canonical_id, doi, pmid, title, abstract, authors_json, journal,
                publication_date, year, source, sources_json, url, oa_pdf_url, pdf_url, article_type,
                citation_count, raw_json, created_at, first_seen_at, last_seen_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (canonical,) + self._article_values(normalized) + (now, now, now),
        )
        self.conn.commit()
        return int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    def _article_values(self, article: Mapping[str, Any]) -> tuple:
        return (
            article.get("doi", ""),
            article.get("pmid", ""),
            article.get("title", "Untitled"),
            article.get("abstract", ""),
            json.dumps(article.get("authors", []), ensure_ascii=False),
            article.get("journal", ""),
            article.get("publication_date") or article.get("date", ""),
            article.get("year", ""),
            article.get("source", "unknown"),
            json.dumps(article.get("sources", []), ensure_ascii=False),
            article.get("url", ""),
            article.get("oa_pdf_url", ""),
            article.get("pdf_url", ""),
            article.get("type", ""),
            _as_int(article.get("citation_count")),
            json.dumps(dict(article), ensure_ascii=False),
        )

    def add_summary(self, article_id: int, model: str, lang: str, short: str = "", structured: dict | None = None, raw: str = "") -> int:
        now = now_iso()
        cur = self.conn.execute(
            "INSERT INTO summaries(article_id, model, lang, summary_short, summary_structured_json, raw_text, created_at) VALUES(?,?,?,?,?,?,?)",
            (article_id, model, lang, short, json.dumps(structured or {}, ensure_ascii=False), raw, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def upsert_score(self, article_id: int, score: Mapping[str, Any]) -> None:
        now = now_iso()
        explanation_payload = {
            "explanation": score.get("explanation", []),
            "deep_evaluation": score.get("deep_evaluation", {}),
        }
        self.conn.execute(
            """
            INSERT INTO scores(article_id, novelty_score, evidence_score, citation_score,
                clinical_relevance_score, risk_score, final_score, label, explanation_json, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(article_id) DO UPDATE SET novelty_score=excluded.novelty_score,
                evidence_score=excluded.evidence_score, citation_score=excluded.citation_score,
                clinical_relevance_score=excluded.clinical_relevance_score, risk_score=excluded.risk_score,
                final_score=excluded.final_score, label=excluded.label,
                explanation_json=excluded.explanation_json, updated_at=excluded.updated_at
            """,
            (
                article_id, score["novelty_score"], score["evidence_score"], score["citation_score"],
                score["clinical_relevance_score"], score["risk_score"], score["final_score"], score["label"],
                json.dumps(explanation_payload, ensure_ascii=False), now,
            ),
        )
        self.conn.commit()

    def get_article(self, identifier: str | int) -> dict | None:
        if isinstance(identifier, int) or str(identifier).isdigit():
            row = self.conn.execute("SELECT a.*, s.* FROM articles a LEFT JOIN scores s ON s.article_id=a.id WHERE a.id=?", (int(identifier),)).fetchone()
        else:
            canonical = canonical_article_key({"doi": identifier}) if str(identifier).lower().startswith(("10.", "doi:", "http")) else identifier
            row = self.conn.execute("SELECT a.*, s.* FROM articles a LEFT JOIN scores s ON s.article_id=a.id WHERE a.canonical_id=? OR a.doi=?", (canonical, str(identifier).lower())).fetchone()
        return _hydrate_row(row) if row else None

    def list_articles(self, limit: int = 20, query: str = "", since: str = "") -> list[dict]:
        sql = "SELECT a.*, s.final_score, s.evidence_score, s.risk_score, s.label FROM articles a LEFT JOIN scores s ON s.article_id=a.id"
        params: list[Any] = []
        clauses = []
        if query:
            clauses.append("(a.title LIKE ? OR a.abstract LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(s.final_score, 0) DESC, a.last_seen_at DESC LIMIT ?"
        params.append(limit)
        return [_hydrate_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def delete_article(self, article_id: int) -> bool:
        exists = self.conn.execute("SELECT id FROM articles WHERE id=?", (article_id,)).fetchone()
        if not exists:
            return False
        for table in ("watchlist_hits", "summaries", "scores", "pdfs", "notes"):
            self.conn.execute(f"DELETE FROM {table} WHERE article_id=?", (article_id,))
        self.conn.execute("DELETE FROM articles WHERE id=?", (article_id,))
        self.conn.commit()
        return True

    def clear_articles(self) -> int:
        count = int(self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        for table in ("watchlist_hits", "summaries", "scores", "pdfs", "notes", "articles"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        return count

    def add_watchlist(self, name: str, query: str, sources: Iterable[str], lang: str = "fr", cadence: str = "manual") -> int:
        """Create a topic, or update its subject if the name already exists.

        Previously this used INSERT OR IGNORE, so re-submitting an existing topic
        silently dropped the new query — there was no way to change a topic's
        subject. The upsert below refreshes query/sources/lang/cadence on a name
        clash, leaving created_at intact, so editing a topic in place works.
        """
        now = now_iso()
        self.conn.execute(
            """
            INSERT INTO watchlists(name, query, sources_json, lang, cadence, created_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                query=excluded.query,
                sources_json=excluded.sources_json,
                lang=excluded.lang,
                cadence=excluded.cadence
            """,
            (name, query, json.dumps(list(sources), ensure_ascii=False), lang, cadence, now),
        )
        row = self.conn.execute("SELECT id FROM watchlists WHERE name=?", (name,)).fetchone()
        self.conn.commit()
        return int(row["id"])

    def update_watchlist(self, watchlist_id: int, *, name: str | None = None,
                         query: str | None = None, sources: Iterable[str] | None = None,
                         lang: str | None = None) -> bool:
        """Edit a topic by id — rename and/or change its subject (query/sources).

        Only the provided fields are written. Returns True if the row existed.
        Raises sqlite3.IntegrityError if `name` collides with another topic.
        """
        fields, params = [], []
        if name is not None:
            fields.append("name=?"); params.append(name)
        if query is not None:
            fields.append("query=?"); params.append(query)
        if sources is not None:
            fields.append("sources_json=?"); params.append(json.dumps(list(sources), ensure_ascii=False))
        if lang is not None:
            fields.append("lang=?"); params.append(lang)
        if not fields:
            return self.conn.execute("SELECT 1 FROM watchlists WHERE id=?", (watchlist_id,)).fetchone() is not None
        params.append(watchlist_id)
        cur = self.conn.execute(f"UPDATE watchlists SET {', '.join(fields)} WHERE id=?", params)
        self.conn.commit()
        return cur.rowcount > 0

    def delete_watchlist(self, watchlist_id: int) -> bool:
        """Remove a topic and its article membership links. Articles are kept."""
        self.conn.execute("DELETE FROM watchlist_hits WHERE watchlist_id=?", (watchlist_id,))
        cur = self.conn.execute("DELETE FROM watchlists WHERE id=?", (watchlist_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_watchlists(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM watchlists ORDER BY name").fetchall()]

    def get_watchlist(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM watchlists WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def record_watchlist_hits(self, watchlist_id: int, article_ids: Iterable[int]) -> list[int]:
        now = now_iso()
        new_hits = []
        for article_id in article_ids:
            try:
                self.conn.execute(
                    "INSERT INTO watchlist_hits(watchlist_id, article_id, seen_at, first_seen) VALUES(?,?,?,1)",
                    (watchlist_id, article_id, now),
                )
                new_hits.append(article_id)
            except sqlite3.IntegrityError:
                self.conn.execute(
                    "UPDATE watchlist_hits SET seen_at=?, first_seen=0 WHERE watchlist_id=? AND article_id=?",
                    (now, watchlist_id, article_id),
                )
        self.conn.execute("UPDATE watchlists SET last_run_at=? WHERE id=?", (now, watchlist_id))
        self.conn.commit()
        return new_hits

    def remove_watchlist_hit(self, watchlist_id: int, article_id: int) -> bool:
        """Detach one article from a watchlist. Returns True if a hit was removed.

        Only the membership link is dropped — the article (and its score, PDF,
        summaries) is left untouched so it still shows up in other views.
        """
        cur = self.conn.execute(
            "DELETE FROM watchlist_hits WHERE watchlist_id=? AND article_id=?",
            (watchlist_id, article_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_watchlist_articles(self, watchlist_id: int, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT a.*, s.final_score, s.evidence_score, s.risk_score, s.label,
                   wh.seen_at, wh.first_seen, wh.watchlist_id
            FROM watchlist_hits wh
            JOIN articles a ON a.id = wh.article_id
            LEFT JOIN scores s ON s.article_id = a.id
            WHERE wh.watchlist_id=?
            ORDER BY wh.seen_at DESC, COALESCE(s.final_score, 0) DESC
            LIMIT ?
            """,
            (watchlist_id, limit),
        ).fetchall()
        return [_hydrate_row(r) for r in rows]

    # ── Meta-analyses ────────────────────────────────────────────────────────
    # A completed meta-analysis is a single narrative Markdown document compiled
    # from many studies. Stored once here (not duplicated across every included
    # study's summary row) so the Research Desk can list past runs and reopen the
    # full document for reading.
    def add_meta_analysis(self, query: str, document_md: str, *, lang: str = "fr",
                          n_studies: int = 0, depth: str = "", md_path: str = "",
                          created_at: str | None = None) -> int:
        """Persist one meta-analysis document; returns its row id.

        ``created_at`` may be supplied so the DB row and the on-disk ``.md`` file
        share an identical timestamp; it defaults to now.
        """
        when = created_at or now_iso()
        cur = self.conn.execute(
            "INSERT INTO meta_analyses(query, lang, n_studies, depth, document_md, md_path, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (query, lang, int(n_studies or 0), depth or "", document_md or "", md_path or "", when),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_meta_analyses(self, limit: int = 100) -> list[dict]:
        """Most-recent-first list of stored meta-analyses (full document included)."""
        rows = self.conn.execute(
            "SELECT id, query, lang, n_studies, depth, document_md, md_path, created_at "
            "FROM meta_analyses ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_meta_analysis(self, meta_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT id, query, lang, n_studies, depth, document_md, md_path, created_at "
            "FROM meta_analyses WHERE id=?",
            (int(meta_id),),
        ).fetchone()
        return dict(row) if row else None

    def delete_meta_analysis(self, meta_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM meta_analyses WHERE id=?", (int(meta_id),))
        self.conn.commit()
        return cur.rowcount > 0
