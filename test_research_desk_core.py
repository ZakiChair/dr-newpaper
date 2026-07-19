import json
import os
import tempfile
import unittest
from pathlib import Path

import normalization
import scoring
from storage import ResearchStore
from structured_summary import parse_structured_summary
import research_exports


class NormalizationTests(unittest.TestCase):
    def test_normalize_doi_strips_url_prefix_case_and_punctuation(self):
        self.assertEqual(normalization.normalize_doi(' https://doi.org/10.1111/ABC. '), '10.1111/abc')
        self.assertEqual(normalization.normalize_doi('doi:10.1000/Xyz'), '10.1000/xyz')

    def test_canonical_key_prefers_doi_then_stable_title_year(self):
        article = {'doi': 'https://doi.org/10.1/ABC', 'title': 'Other'}
        self.assertEqual(normalization.canonical_article_key(article), 'doi:10.1/abc')
        article = {'title': '  A  Big Study! ', 'date': '2026-04-01', 'authors': ['Ada Lovelace']}
        self.assertEqual(normalization.canonical_article_key(article), 'title:2026:ada_lovelace:a_big_study')

    def test_merge_articles_keeps_best_metadata_and_sources(self):
        first = {'title': 'Study', 'doi': '10.1/a', 'abstract': '', 'source': 'PubMed'}
        second = {'title': 'Study', 'doi': 'https://doi.org/10.1/A', 'abstract': 'Long abstract', 'source': 'OpenAlex', 'oa_pdf_url': 'https://x/a.pdf'}
        merged = normalization.merge_article_records(first, second)
        self.assertEqual(merged['doi'], '10.1/a')
        self.assertEqual(merged['abstract'], 'Long abstract')
        self.assertEqual(merged['oa_pdf_url'], 'https://x/a.pdf')
        self.assertEqual(merged['sources'], ['PubMed', 'OpenAlex'])


class StorageAndScoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'research.db'
        self.store = ResearchStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_upsert_deduplicates_by_canonical_doi_and_merges_sources(self):
        a1 = {'title': 'Study', 'doi': '10.1/a', 'source': 'PubMed', 'abstract': 'A'}
        a2 = {'title': 'Study', 'doi': 'https://doi.org/10.1/A', 'source': 'OpenAlex', 'journal': 'J'}
        id1 = self.store.upsert_article(a1, query='minoxidil')
        id2 = self.store.upsert_article(a2, query='minoxidil')
        self.assertEqual(id1, id2)
        articles = self.store.list_articles(limit=10)
        self.assertEqual(len(articles), 1)
        self.assertEqual(json.loads(articles[0]['sources_json']), ['PubMed', 'OpenAlex'])
        self.assertEqual(articles[0]['journal'], 'J')

    def test_watchlist_run_state_marks_new_hits_once(self):
        wid = self.store.add_watchlist('derm', 'minoxidil', ['pubmed'], 'fr')
        aid = self.store.upsert_article({'title': 'Study', 'doi': '10.1/a', 'source': 'PubMed'}, query='minoxidil')
        first = self.store.record_watchlist_hits(wid, [aid])
        second = self.store.record_watchlist_hits(wid, [aid])
        self.assertEqual(first, [aid])
        self.assertEqual(second, [])

    def test_scoring_distinguishes_hot_rct_from_risky_preprint(self):
        hot = scoring.score_article({'title': 'Randomized clinical trial', 'type': 'randomized trial', 'date': '2026-06-01', 'abstract': 'human trial n=240 clinical outcome'})
        risk = scoring.score_article({'title': 'Small medRxiv preprint', 'source': 'medRxiv', 'type': 'preprint', 'date': '2020', 'abstract': ''})
        self.assertGreater(hot['final_score'], risk['final_score'])
        self.assertIn(hot['label'], {'HOT', 'NEW'})
        self.assertEqual(risk['label'], 'RISK')


class ArticleModelRoundTripTests(unittest.TestCase):
    """Step 2: the storage round-trip must stop dropping citation_count / type /
    oa-metadata so the score and downstream signals see the full article."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'research.db'
        self.store = ResearchStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_citation_count_survives_round_trip_and_lifts_score(self):
        # Citations live ONLY in the structured field (no number in the text),
        # so a non-zero citation_score proves the FIELD round-tripped, not text leakage.
        aid = self.store.upsert_article({
            'title': 'Effect of X on Y', 'doi': '10.1/cit',
            'abstract': 'A study of X and Y outcomes.',
            'source': 'semantic_scholar', 'citation_count': 250,
        }, query='x')
        row = self.store.get_article(aid)                 # the production path
        self.assertEqual(row['citation_count'], 250)
        self.assertGreater(scoring.score_article(row)['citation_score'], 0)

    def test_hydrate_reexposes_type_alias_and_raw_json_only_fields(self):
        aid = self.store.upsert_article({
            'title': 'Preprint study', 'doi': '10.1/pp',
            'type': 'preprint', 'oa_status': 'green', 'source': 'biorxiv',
        }, query='x')
        row = self.store.get_article(aid)
        self.assertEqual(row['type'], 'preprint')          # alias from article_type column
        self.assertEqual(row['oa_status'], 'green')         # restored from raw_json (no column)
        # list_articles hydrates the same way
        listed = self.store.list_articles(limit=5)
        self.assertEqual(listed[0]['type'], 'preprint')

    def test_empty_column_does_not_shadow_richer_raw_json_value(self):
        # Defensive: a freshly-defaulted citation_count column must not clobber a
        # real value carried in raw_json (the migration-precedence trap).
        aid = self.store.upsert_article({'title': 'T', 'doi': '10.1/z', 'citation_count': 7}, query='x')
        self.store.conn.execute("UPDATE articles SET citation_count=0 WHERE id=?", (aid,))
        self.store.conn.commit()
        self.assertEqual(self.store.get_article(aid)['citation_count'], 7)

    def test_citation_count_migration_backfills_legacy_db(self):
        # Build a DB that predates the column, with the value only in raw_json.
        legacy = Path(self.tmp.name) / 'legacy.db'
        import sqlite3
        c = sqlite3.connect(str(legacy))
        # The real pre-citation_count schema: every column EXCEPT citation_count.
        c.executescript("""
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_id TEXT UNIQUE NOT NULL,
                doi TEXT, pmid TEXT, title TEXT NOT NULL, abstract TEXT,
                authors_json TEXT DEFAULT '[]', journal TEXT, publication_date TEXT, year TEXT,
                source TEXT, sources_json TEXT DEFAULT '[]', url TEXT, oa_pdf_url TEXT, pdf_url TEXT,
                article_type TEXT, raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
        """)
        c.execute("INSERT INTO articles(canonical_id, doi, title, raw_json, created_at, first_seen_at, last_seen_at)"
                  " VALUES('doi:10.1/m','10.1/m','M', '{\"citation_count\": 42}', 't','t','t')")
        c.commit(); c.close()
        store = ResearchStore(legacy)            # triggers _migrate
        try:
            cols = {r[1] for r in store.conn.execute("PRAGMA table_info(articles)")}
            self.assertIn('citation_count', cols)
            self.assertEqual(store.get_article('10.1/m')['citation_count'], 42)  # backfilled
        finally:
            store.close()

    def test_research_article_from_raw_carries_type_and_citations(self):
        from research_terminal import ResearchArticle
        art = ResearchArticle.from_raw({
            'title': 'T', 'publication_date': '2026-01-01', 'authors_json': '["Ada"]',
            'type': 'clinical_trial', 'pmid': '999', 'citation_count': '15', 'oa_status': 'gold',
        })
        self.assertEqual(art.type, 'clinical_trial')
        self.assertEqual(art.pmid, '999')
        self.assertEqual(art.citation_count, 15)            # coerced str -> int
        self.assertEqual(art.date, '2026-01-01')            # publication_date -> date
        self.assertEqual(art.authors, ['Ada'])
        self.assertIn('citation_count', art.to_payload())   # to_db carries everything


class DefaultsHarmonizationTests(unittest.TestCase):
    """Every front end must read its search defaults from config.py, so the same
    query can't silently produce different sources/counts per interface."""

    def test_config_exposes_canonical_search_defaults(self):
        import config
        self.assertEqual(config.DEFAULT_SOURCES, ['pubmed', 'crossref', 'openalex'])
        self.assertEqual(config.DEFAULT_MAX_RESULTS, 5)
        # the deep set is the superset the deep pipeline actually honours
        self.assertTrue(set(config.DEFAULT_SOURCES).issubset(config.DEFAULT_DEEP_SOURCES))

    def test_tui_defaults_track_config(self):
        import config, research_tui
        state = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[])
        self.assertEqual(state.search_max, config.DEFAULT_MAX_RESULTS)
        # the "balanced" sensitivity rung IS the canonical default source list
        balanced = dict((lbl, src) for lbl, src in research_tui.SENSITIVITY_LEVELS)['Équilibrée']
        self.assertEqual(balanced, config.DEFAULT_SOURCES)
        exhaustive = dict((lbl, src) for lbl, src in research_tui.SENSITIVITY_LEVELS)['Exhaustive']
        self.assertEqual(exhaustive, config.DEFAULT_DEEP_SOURCES)

    def test_bot_defaults_track_config(self):
        import config
        try:
            import bot
        except Exception as exc:  # telegram lib absent in some envs — skip, don't fail
            self.skipTest(f"bot import unavailable: {exc}")
        self.assertEqual(bot.DEFAULT_MAX, config.DEFAULT_MAX_RESULTS)
        self.assertEqual(bot.DEFAULT_LANG, config.DEFAULT_LANG)
        self.assertEqual(bot.get_settings(987654)['sources'], config.DEFAULT_SOURCES)


class StructuredSummaryAndExportTests(unittest.TestCase):
    def test_parse_structured_summary_extracts_json_from_fenced_text(self):
        text = 'before```json\n{"study_type":"RCT","key_findings":["x"],"evidence_grade":"high"}\n```after'
        parsed = parse_structured_summary(text)
        self.assertEqual(parsed['study_type'], 'RCT')
        self.assertEqual(parsed['evidence_grade'], 'high')

    def test_exports_create_markdown_csv_bibtex(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            article = {
                'id': 1, 'title': 'A Study', 'doi': '10.1/a', 'authors_json': json.dumps(['Ada Lovelace', 'Alan Turing']),
                'journal': 'Journal', 'publication_date': '2026-01-02', 'abstract': 'Abs', 'url': 'https://example.test',
                'final_score': 88, 'evidence_score': 40, 'risk_score': 0, 'label': 'HOT'
            }
            written = research_exports.export_research_dossier(out, 'minoxidil', [article], since='30d')
            self.assertTrue((out / 'report.md').exists())
            self.assertTrue((out / 'evidence_table.csv').exists())
            self.assertTrue((out / 'bibliography.bib').exists())
            self.assertIn('@article{', (out / 'bibliography.bib').read_text())
            self.assertEqual(set(written.keys()), {'report', 'csv', 'bibtex', 'json'})


if __name__ == '__main__':
    unittest.main()
