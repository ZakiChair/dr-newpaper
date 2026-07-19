import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import research_terminal
from storage import ResearchStore


class ResearchTerminalCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'research.db'

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = research_terminal.main([*argv, '--db', str(self.db_path), '--no-color'])
        return code, buf.getvalue()

    def seed(self):
        store = ResearchStore(self.db_path)
        aid = store.upsert_article({
            'title': 'Randomized trial of oral minoxidil safety',
            'doi': '10.1/minox',
            'source': 'PubMed',
            'date': '2026-01-01',
            'abstract': 'human randomized clinical trial n=240 safety outcome',
            'authors': ['Ada Lovelace'],
            'journal': 'Derm Journal',
            'url': 'https://example.test',
        }, query='minoxidil')
        import scoring
        store.upsert_score(aid, scoring.score_article(store.get_article(aid)))
        store.close()
        return aid

    def test_tape_and_article_read_from_sqlite(self):
        aid = self.seed()
        code, tape = self.run_cli(['tape', '--limit', '5'])
        self.assertEqual(code, 0)
        self.assertIn('ARTICLE TAPE', tape)
        self.assertIn('Randomized trial', tape)
        code, article = self.run_cli(['article', str(aid)])
        self.assertEqual(code, 0)
        self.assertIn('ARTICLE DETAIL', article)
        self.assertIn('Derm Journal', article)

    def test_watch_add_list_and_digest_export(self):
        self.seed()
        code, out = self.run_cli(['watch', 'add', 'derm', 'minoxidil', '--sources', 'pubmed'])
        self.assertEqual(code, 0)
        self.assertIn('Watchlist ajoutée', out)
        code, out = self.run_cli(['watch', 'list'])
        self.assertIn('derm', out)
        export_dir = Path(self.tmp.name) / 'dossier'
        code, out = self.run_cli(['digest', 'minoxidil', '--output-dir', str(export_dir)])
        self.assertEqual(code, 0)
        self.assertTrue((export_dir / 'report.md').exists())
        self.assertIn('Dossier exporté', out)

    @patch('research_terminal.run_search')
    def test_search_save_persists_and_scores_results(self, fake_run_search):
        fake_run_search.return_value = [research_terminal.ResearchArticle(
            title='New randomized trial', source='PubMed', date='2026-01-01', doi='10.1/new',
            abstract='human randomized clinical trial n=120 efficacy outcome'
        )]
        code, out = self.run_cli(['search', 'minoxidil', '--max', '1', '--sources', 'pubmed', '--save'])
        self.assertEqual(code, 0)
        self.assertIn('ARTICLES 1', out)
        store = ResearchStore(self.db_path)
        rows = store.list_articles(limit=10)
        store.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['label'], 'HOT')


if __name__ == '__main__':
    unittest.main()
