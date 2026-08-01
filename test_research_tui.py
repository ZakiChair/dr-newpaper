import contextlib
import curses
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from storage import ResearchStore
import config
import scoring
import research_tui
import meta_analysis


class AdvancedEvaluationTests(unittest.TestCase):
    def test_methodology_financing_conflict_and_blinding_are_detected(self):
        article = {
            'title': 'A double-blind randomized placebo-controlled trial funded by PharmaCorp',
            'doi': '10.1/conflict',
            'date': '2026-01-01',
            'abstract': 'This double-blind randomized placebo-controlled clinical trial enrolled n=420 patients. Primary endpoint was all-cause mortality at 12 months. Random sequence generation and allocation concealment were described. Funding was provided by PharmaCorp and two authors report consulting fees and stock ownership. The sponsor participated in study design and data analysis.'
        }
        ev = scoring.evaluate_article_deep(article)
        self.assertEqual(ev['methodology']['study_design'], 'randomized_controlled_trial')
        self.assertEqual(ev['methodology']['blinding'], 'double_blind')
        self.assertEqual(ev['methodology']['control'], 'placebo_controlled')
        self.assertEqual(ev['methodology']['randomization'], 'reported')
        self.assertEqual(ev['methodology']['allocation_concealment'], 'reported')
        self.assertIn('mortality', ev['outcomes']['primary_outcomes'])
        self.assertEqual(ev['funding']['funding_detected'], True)
        self.assertIn('PharmaCorp', ev['funding']['suspected_funders'])
        self.assertGreaterEqual(ev['conflicts']['conflict_risk_score'], 25)
        self.assertIn('sponsor_involved_in_analysis', ev['red_flags'])
        self.assertIn('interpretation', ev)
        self.assertIn('completeness', ev)
        self.assertGreater(ev['completeness']['score'], 80)
        self.assertIn('study_population', ev['pico'])
        self.assertIn('GRADE', ev['evidence_grade']['framework'])
        self.assertIn('CONSORT', ev['reporting_quality']['frameworks_considered'])

    def test_theme_config_supports_bloomberg_matrix_and_cute(self):
        self.assertEqual(research_tui.theme_config('bloomberg')['accent_name'], 'amber terminal')
        self.assertEqual(research_tui.theme_config('matrix')['accent_name'], 'phosphor green')
        self.assertEqual(research_tui.theme_config('cute')['accent_name'], 'soft pink')
        self.assertEqual(research_tui.theme_config('unknown')['name'], 'bloomberg')
        self.assertIn('glyph', research_tui.theme_config('cute'))
        self.assertIn('banner', research_tui.theme_config('matrix'))
        self.assertIn('front_palette', research_tui.theme_config('bloomberg'))
        self.assertIn('mascot', research_tui.theme_config('bloomberg'))
        self.assertGreaterEqual(len(research_tui.theme_config('matrix')['mascot']), 6)
        self.assertIn('frame_style', research_tui.theme_config('cute'))
        self.assertEqual(research_tui.theme_config('bloomberg')['mascot_name'], 'Quill Terminal')
        self.assertEqual(research_tui.theme_config('matrix')['mascot_name'], 'Monolith Lynx')
        self.assertEqual(research_tui.theme_config('cute')['mascot_name'], 'Paper Mochi')
        for theme in ('bloomberg', 'matrix', 'cute'):
            mascot = research_tui.theme_config(theme)['mascot']
            self.assertEqual(len(mascot), 6)
            self.assertTrue(all(14 <= len(line) <= 34 for line in mascot))
            self.assertTrue(any('📄' in line for line in mascot))
            joined = '\n'.join(mascot)
            self.assertTrue(any(ch in joined for ch in '╭╮╰╯╔╗╚╝'))
            self.assertFalse(any(raw in joined for raw in ('/\\_/\\', '( o.o )', '> ^ <', '(\\_/)')))

    def test_front_palette_is_complete_and_theme_specific(self):
        required_roles = {'brand', 'emblem', 'meta', 'controls', 'mission', 'texture', 'border', 'background'}
        palettes = {theme: research_tui.theme_front_palette(theme) for theme in ('bloomberg', 'matrix', 'cute')}
        for palette in palettes.values():
            self.assertTrue(required_roles.issubset(palette))
            self.assertTrue(all(palette[role] for role in required_roles))
        self.assertEqual(palettes['matrix']['brand'], 'green')
        self.assertEqual(palettes['cute']['brand'], 'magenta')
        self.assertNotEqual(palettes['bloomberg']['brand'], palettes['matrix']['brand'])

    def test_relevance_and_recent_sorting_prioritizes_recent_hot_articles(self):
        rows = [
            {'title': 'Old weak paper', 'publication_date': '2018-01-01', 'final_score': 90, 'risk_score': 5},
            {'title': 'Recent strong paper', 'publication_date': '2026-06-01', 'final_score': 80, 'risk_score': 0},
            {'title': 'Risky recent preprint', 'publication_date': '2026-06-02', 'final_score': 50, 'risk_score': 35},
        ]
        sorted_rows = research_tui.sort_articles_for_ui(rows, mode='recent_relevant')
        self.assertEqual(sorted_rows[0]['title'], 'Recent strong paper')
        self.assertEqual(sorted_rows[-1]['title'], 'Risky recent preprint')


class ResearchTuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'research.db'
        # Never let meta-analysis runs write into the user's real Dossier.
        self._prev_meta_dir = os.environ.get('DR_NEWPAPER_META_DIR')
        os.environ['DR_NEWPAPER_META_DIR'] = str(Path(self.tmp.name) / 'meta')
        store = ResearchStore(self.db_path)
        self.article_id = store.upsert_article({
            'title': 'Randomized terminal interface study',
            'doi': '10.1/tui',
            'source': 'PubMed',
            'date': '2026-01-01',
            'abstract': 'human double-blind randomized placebo-controlled clinical trial n=120 efficacy outcome funded by NIH',
            'authors': ['Ada Lovelace'],
            'journal': 'Interface Journal',
            'url': 'https://example.test/tui',
        }, query='terminal')
        row = store.get_article(self.article_id)
        store.upsert_score(self.article_id, scoring.score_article(row))
        store.add_watchlist('terminal', 'terminal interface', ['pubmed'], 'fr')
        store.close()

    def tearDown(self):
        if self._prev_meta_dir is None:
            os.environ.pop('DR_NEWPAPER_META_DIR', None)
        else:
            os.environ['DR_NEWPAPER_META_DIR'] = self._prev_meta_dir
        self.tmp.cleanup()

    def test_load_state_separates_current_results_from_saved_library(self):
        state = research_tui.load_state(self.db_path, limit=20)
        self.assertEqual(len(state.current_articles), 0)
        self.assertEqual(len(state.saved_articles), 1)
        self.assertEqual(state.active_tab, 'current')
        self.assertIn('Aucun résultat courant', research_tui.render_detail_text(state, width=100))
        state.active_tab = 'saved'
        self.assertIn('Randomized terminal interface study', research_tui.render_detail_text(state, width=100))

    def test_demo_render_contains_current_saved_evaluation_sections(self):
        text = research_tui.render_demo(self.db_path, width=100)
        self.assertIn('CURRENT RESULTS', text)
        self.assertIn('SAVED ARTICLES', text)
        self.assertIn('EVALUATION', text)
        self.assertIn('Randomized terminal interface study', text)
        self.assertIn('CURRENT', text)
        self.assertIn('Saved', text)
        self.assertIn('Evaluation', text)

    def test_tab_bar_shows_current_saved_evaluation_and_active_marker(self):
        bar = research_tui.render_tab_bar('saved', 'articles', width=100, theme='matrix')
        self.assertIn('Current', bar)
        self.assertIn('SAVED', bar)
        self.assertIn('Evaluation', bar)
        self.assertIn('Watchlist', bar)
        self.assertIn('▣ SAVED', bar)
        self.assertIn('Focus:ARTICLES', bar)

    def test_logo_block_contains_ascii_brand_version_controls_and_theme(self):
        block = research_tui.render_logo_block('matrix', width=100)
        text = '\n'.join(block)
        self.assertIn('Dr · NewPaper', text)  # branded wordmark with the dot between Dr and NewPaper
        self.assertIn(research_tui.APP_VERSION, text)
        self.assertIn('S Search', text)
        self.assertIn('M Meta', text)
        self.assertIn('P PDF', text)
        self.assertIn('X Delete', text)
        self.assertIn('Z Clear', text)
        self.assertIn('theme:matrix', text)
        self.assertGreaterEqual(len(block), 5)
        self.assertTrue(all(len(line) <= 100 for line in block))

    def test_theme_mascot_appears_only_inside_logo_block_in_demo(self):
        text = research_tui.render_demo(self.db_path, width=100, theme='cute')
        cute_mascot = research_tui.theme_config('cute')['mascot']
        marker = next(line for line in cute_mascot if '📄' in line).strip()
        self.assertEqual(text.count(marker), 1)

    def test_evaluate_selected_article_opens_evaluation_tab(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'
        result = research_tui.evaluate_selected_article(state)
        self.assertIn('Evaluated article', result)
        self.assertEqual(state.active_tab, 'evaluation')
        panel = research_tui.render_evaluation_text(state, width=100)
        self.assertIn('METHODOLOGY', panel)
        self.assertIn('Blinding:', panel)
        self.assertIn('Funding:', panel)
        self.assertIn('Conflict risk:', panel)

    def test_recent_domain_query_builds_recent_search_and_can_be_mocked(self):
        query = research_tui.build_recent_domain_query('dermatology', days=30)
        self.assertIn('dermatology', query)
        self.assertIn('last 30 days', query)

    def test_new_search_replaces_current_results_but_preserves_saved_library(self):
        def fake_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            return [research_tui.ResearchArticle(
                title='Recent dermatology breakthrough',
                source='PubMed',
                date='2026-06-01',
                doi='10.1/recent',
                abstract='human randomized clinical trial n=300 safety outcome'
            )]

        state = research_tui.load_state(self.db_path, limit=20)
        msg = research_tui.search_and_store_from_tui(
            state,
            'dermatology',
            max_results=1,
            sources=['pubmed'],
            runner=fake_runner,
        )
        self.assertIn('1 article', msg)
        self.assertEqual(state.active_tab, 'current')
        self.assertEqual([a['title'] for a in state.current_articles], ['Recent dermatology breakthrough'])
        self.assertTrue(any(a['title'] == 'Randomized terminal interface study' for a in state.saved_articles))

    def test_prompt_mode_is_cancelable_and_enter_returns_action_value(self):
        state = research_tui.load_state(self.db_path, limit=20)
        research_tui.begin_prompt(state, 'search', 'Search articles')
        for ch in 'cancer':
            action, value = research_tui.handle_prompt_key(state, ord(ch))
            self.assertIsNone(action)
        self.assertEqual(state.prompt_value, 'cancer')
        research_tui.handle_prompt_key(state, 27)
        self.assertEqual(state.mode, 'normal')
        self.assertEqual(state.status, 'Prompt cancelled — interface ready')

        research_tui.begin_prompt(state, 'search', 'Search articles', 'default query')
        action, value = research_tui.handle_prompt_key(state, 10)
        self.assertEqual((action, value), ('search', 'default query'))
        self.assertEqual(state.mode, 'normal')

    def test_active_rows_and_sync_keep_layout_stable_after_search(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'
        research_tui._sync_articles_view(state)
        self.assertEqual(len(state.articles), 1)
        state.current_articles = [{'title': 'New result', 'final_score': 70, 'risk_score': 0, 'publication_date': '2026'}]
        state.active_tab = 'current'
        research_tui._sync_articles_view(state)
        self.assertEqual(state.articles[0]['title'], 'New result')
        self.assertEqual(state.selected_article, 0)

    def test_visible_window_keeps_selected_row_in_view(self):
        self.assertEqual(research_tui.visible_window(total=20, selected=0, capacity=5), (0, 5))
        self.assertEqual(research_tui.visible_window(total=20, selected=9, capacity=5), (7, 12))
        self.assertEqual(research_tui.visible_window(total=20, selected=19, capacity=5), (15, 20))
        start, end = research_tui.visible_window(total=20, selected=14, capacity=6)
        self.assertLessEqual(start, 14)
        self.assertGreater(end, 14)

    def test_article_selection_clamps_without_negative_index_on_empty_list(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.current_articles = []
        state.active_tab = 'current'
        state.selected_article = 7
        research_tui._sync_articles_view(state)
        self.assertEqual(state.selected_article, 0)
        self.assertEqual(state.articles, [])

    def test_theme_shortcut_cycles_forward_backward_and_wraps(self):
        state = research_tui.load_state(self.db_path, limit=20)
        order = research_tui.THEME_ORDER
        state.theme = order[0]
        self.assertEqual(research_tui.cycle_theme(state), order[1])
        self.assertIn(order[1], state.status)
        # Walk forward through every theme and confirm it wraps back to the first.
        for nxt in order[2:] + order[:1]:
            self.assertEqual(research_tui.cycle_theme(state), nxt)
        # Backward from the first wraps to the last.
        self.assertEqual(research_tui.cycle_theme(state, direction=-1), order[-1])

    def test_meta_analysis_from_tui_stores_synthetic_articles_and_summary_note(self):
        def fake_meta(query, max_articles=8, deep=False, lang='fr'):
            return {
                'query': query,
                'summary': 'Résumé méta-analyse: effet favorable mais hétérogénéité élevée.',
                'n_studies': 2,
                'articles': [
                    {'title': 'Meta included RCT A', 'doi': '10.1/meta-a', 'date': '2026-01-01', 'abstract': 'randomized placebo clinical trial n=140 mortality hazard ratio 0.80 95% CI'},
                    {'title': 'Meta included RCT B', 'doi': '10.1/meta-b', 'date': '2025-01-01', 'abstract': 'double blind controlled human study n=220 safety efficacy confidence interval'},
                ],
                'lang': 'fr',
            }

        state = research_tui.load_state(self.db_path, limit=20)
        msg = research_tui.run_meta_analysis_from_tui(state, 'minoxidil safety', max_articles=2, runner=fake_meta)
        # The run now lands on the readable Meta-Analyses tab with the document open.
        self.assertIn('Meta-analysis ready', msg)
        self.assertEqual(state.active_tab, 'meta')
        self.assertEqual(len(state.meta_analyses), 1)
        self.assertEqual(state.meta_analyses[0]['n_studies'], 2)
        self.assertIn('hétérogénéité', state.meta_document)
        # …and the full synthesis is what renders in the detail pane (not truncated).
        doc = research_tui.render_meta_document_text(state, width=110)
        self.assertIn('effet favorable', doc)
        # The included studies are still stored and appraisable in the Evaluation tab.
        self.assertEqual(len(state.current_articles), 2)
        state.active_tab = 'evaluation'
        panel = research_tui.render_evaluation_text(state, width=110)
        self.assertIn('PICO / COMPLETENESS', panel)
        # A readable .md was written to the (tmp-redirected) library.
        self.assertTrue(state.meta_analyses[0]['md_path'])
        self.assertTrue(Path(state.meta_analyses[0]['md_path']).exists())

    def test_meta_run_reports_meta_stages_and_streams_studies(self):
        # The long meta run must feed the SAME progress machinery as search, so
        # the band animates and the included studies scroll in.
        chan = research_tui.ProgressChannel()

        def fake_meta(query, max_articles=8, deep=False, lang='fr', progress=None):
            if progress:
                progress("Recherche des études…")
                progress("PDF 1/2 — Study A")
                progress("Compilation du document (2 études)…")
            return {'query': query, 'summary': 'meta summary', 'n_studies': 2, 'lang': 'fr',
                    'articles': [
                        {'title': 'Meta RCT A', 'doi': '10.1/ma', 'date': '2026-01-01',
                         'abstract': 'randomized controlled trial n=200 mortality outcome'},
                        {'title': 'Meta RCT B', 'doi': '10.1/mb', 'date': '2025-01-01',
                         'abstract': 'controlled human study n=80 safety'},
                    ]}

        state = research_tui.load_state(self.db_path, limit=20)
        research_tui.run_meta_analysis_from_tui(state, 'q', max_articles=2, runner=fake_meta,
                                                progress_cb=chan.report, result_cb=chan.add_result)
        snap = chan.snapshot()
        reported = set(snap['history']) | {snap['stage']}
        self.assertTrue({'collect', 'extract', 'synthesize'}.issubset(reported))  # meta phases reported
        self.assertEqual(snap['stage'], 'score')                                  # ends scoring/storing
        self.assertEqual(len(snap['results']), 2)                                 # both studies streamed
        # search_progress_snapshot now lights up for meta (channel is written)
        state.busy = True
        state.task = research_tui.BackgroundTask(kind='meta', verb='Meta-analyzing', query='q',
                                                 worker=lambda: None, apply=lambda l, r: 'done',
                                                 progress=chan)
        self.assertIsNotNone(research_tui.search_progress_snapshot(state))
        band = '\n'.join(research_tui.format_progress_band(snap, 'bloomberg', stages=research_tui.META_STAGES, width=100))
        self.assertIn('Synthesize', band)                                         # META checklist, not search

    def test_download_selected_article_uses_injected_downloader_and_records_pdf_status(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'

        def fake_downloader(doi, title=''):
            return {'success': True, 'path': '/tmp/fake-paper.pdf', 'method': 'unit', 'error': ''}

        msg = research_tui.download_selected_article(state, downloader=fake_downloader)
        self.assertIn('PDF ready', msg)
        store = ResearchStore(self.db_path)
        try:
            row = store.conn.execute('SELECT local_path, extraction_status FROM pdfs WHERE article_id=?', (self.article_id,)).fetchone()
            self.assertEqual(row['local_path'], '/tmp/fake-paper.pdf')
            self.assertEqual(row['extraction_status'], 'downloaded')
        finally:
            store.close()

    def test_follow_topic_from_tui_creates_watchlist(self):
        state = research_tui.load_state(self.db_path, limit=20)
        msg = research_tui.follow_topic_from_tui(state, 'longevity', 'rapamycin longevity')
        self.assertIn('Following topic', msg)
        self.assertTrue(any(w['name'] == 'longevity' for w in state.watchlists))
        self.assertEqual(state.active_tab, 'watchlist')
        self.assertEqual(state.focus, 'watchlists')

    def test_topic_slug_derives_dash_name_from_subject(self):
        self.assertEqual(research_tui.topic_slug('Oral Minoxidil Safety!'), 'oral-minoxidil-safety')
        self.assertEqual(research_tui.topic_slug(''), 'topic')
        self.assertLessEqual(len(research_tui.topic_slug('a very long subject ' * 5)), 24)

    def test_topic_name_is_never_part_of_the_searched_query(self):
        # The topic NAME is a label; only the underlying subject is ever searched.
        state = research_tui.load_state(self.db_path, limit=20)
        research_tui.begin_topic_prompt(state)
        research_tui.topic_subject_entered(state, 'minoxidil alopecia')   # the search
        research_tui.topic_name_entered(state, 'Capillaria')              # a distinct label
        watch = next(w for w in state.watchlists if w['name'] == 'Capillaria')
        self.assertEqual(watch['query'], 'minoxidil alopecia')            # subject stored, not the name
        self.assertNotIn('capillaria', watch['query'].lower())

        captured = {}
        def fake_runner(query, max_results, lang, sources, deep, allow_scihub, **kw):
            captured['query'] = query
            return []
        # Refresh the topic exactly as the Enter-on-watchlist handler does.
        research_tui.search_and_store_from_tui(
            state, str(watch.get('query') or ''), max_results=8,
            sources=['pubmed'], runner=fake_runner)
        self.assertEqual(captured['query'], 'minoxidil alopecia')
        self.assertNotIn('capillaria', captured['query'].lower())         # name never searched

    def test_topic_name_entered_does_not_fall_back_to_name_as_subject(self):
        # Guard: with no subject captured, the name must NOT become the query.
        state = research_tui.load_state(self.db_path, limit=20)
        state.pending_topic_subject = ''
        research_tui.topic_name_entered(state, 'JustALabel')
        watch = next((w for w in state.watchlists if w['name'] == 'JustALabel'), None)
        if watch is not None:                                            # if created at all,
            self.assertNotEqual(watch['query'], 'JustALabel')            # the query is never the name

    def test_topic_creation_asks_subject_first_then_name_adapts(self):
        # The bug: the name stayed stuck on a placeholder and never reflected the
        # subject. Now the subject is asked first and the name defaults to its slug.
        state = research_tui.load_state(self.db_path, limit=20)
        state.last_query = ''                       # fresh user, no prior search
        research_tui.begin_topic_prompt(state)
        self.assertEqual(state.prompt_action, 'topic_subject')   # subject FIRST
        research_tui.topic_subject_entered(state, 'oral minoxidil safety in women')
        self.assertEqual(state.prompt_action, 'topic_name')
        self.assertEqual(state.prompt_default, 'oral-minoxidil-safety-in')  # name pre-filled from subject
        self.assertEqual(state.pending_topic_subject, 'oral minoxidil safety in women')
        # User accepts the slug name (empty input → slug fallback)
        research_tui.topic_name_entered(state, '')
        made = next(w for w in state.watchlists if w['query'] == 'oral minoxidil safety in women')
        self.assertTrue(made['name'].startswith('oral-minoxidil'))
        self.assertNotEqual(made['name'], 'new-topic')           # the reported symptom is gone

    def test_topic_creation_custom_name_overrides_slug(self):
        state = research_tui.load_state(self.db_path, limit=20)
        research_tui.begin_topic_prompt(state)
        research_tui.topic_subject_entered(state, 'rapamycin longevity')
        research_tui.topic_name_entered(state, 'aging')          # explicit custom name
        made = next(w for w in state.watchlists if w['query'] == 'rapamycin longevity')
        self.assertEqual(made['name'], 'aging')
        self.assertEqual(state.pending_topic_subject, '')        # cleared after creation

    def test_topic_creation_follows_the_configured_output_language(self):
        # A new topic's persisted lang must follow the app-wide language knob,
        # not a hardcoded default — this was the bug: every topic was born
        # French regardless of what the config screen said.
        state = research_tui.load_state(self.db_path, limit=20)
        state.lang_idx = 1  # English
        research_tui.begin_topic_prompt(state)
        research_tui.topic_subject_entered(state, 'rapamycin longevity')
        research_tui.topic_name_entered(state, 'aging-en')
        made = next(w for w in state.watchlists if w['name'] == 'aging-en')
        self.assertEqual(made['lang'], 'en')

    def test_delete_selected_saved_article_removes_it_from_storage_and_state(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'
        state.selected_article = 0
        msg = research_tui.delete_selected_saved_article(state)
        self.assertIn('Deleted saved study', msg)
        self.assertFalse(any(a['title'] == 'Randomized terminal interface study' for a in state.saved_articles))
        store = ResearchStore(self.db_path)
        try:
            self.assertIsNone(store.get_article(self.article_id))
        finally:
            store.close()

    def test_clear_saved_articles_removes_all_articles_but_keeps_watchlists(self):
        state = research_tui.load_state(self.db_path, limit=20)
        msg = research_tui.clear_saved_articles(state)
        self.assertIn('Cleared 1 saved studies', msg)
        self.assertEqual(state.saved_articles, [])
        self.assertEqual(state.current_articles, [])
        self.assertTrue(any(w['name'] == 'terminal' for w in state.watchlists))
        store = ResearchStore(self.db_path)
        try:
            self.assertEqual(store.list_articles(limit=20), [])
            self.assertEqual(len(store.list_watchlists()), 1)
        finally:
            store.close()

    def test_watchlist_search_suppresses_runner_noise_and_links_articles_to_theme(self):
        state = research_tui.load_state(self.db_path, limit=20)
        research_tui.follow_topic_from_tui(state, 'alopecia', 'oral minoxidil alopecia')
        state.active_tab = 'watchlist'
        state.focus = 'watchlists'

        def noisy_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            print('SEARCHING PUBMED noisy provider line')
            print('FOUND STUDY noisy provider line')
            return [research_tui.ResearchArticle(
                title='Oral minoxidil watchlist study',
                source='PubMed',
                date='2026-06-01',
                doi='10.1/watchlist',
                abstract='randomized clinical trial n=80 oral minoxidil alopecia safety efficacy',
            )]

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            msg = research_tui.search_and_store_from_tui(state, 'oral minoxidil alopecia', max_results=1, runner=noisy_runner)
        self.assertEqual(captured.getvalue(), '')
        self.assertIn('1 article stored', msg)
        self.assertEqual(state.active_tab, 'watchlist')
        self.assertTrue(any(row['title'] == 'Oral minoxidil watchlist study' for row in state.watchlist_articles))
        panel = research_tui.render_watchlist_text(state, width=100)
        self.assertIn('WATCHLIST THEMES', panel)
        self.assertIn('STUDIES FOR THIS THEME', panel)
        self.assertIn('Oral minoxidil watchlist study', panel)

    def test_add_selected_article_to_watchlist_from_results(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'           # the fixture study lives in SAVED
        state.selected_article = 0
        state.selected_watchlist = 0         # 'terminal' theme highlighted
        msg = research_tui.add_selected_to_watchlist(state)
        self.assertIn("Added to watchlist 'terminal'", msg)
        store = ResearchStore(self.db_path)
        try:
            wid = int(store.list_watchlists()[0]['id'])
            rows = store.list_watchlist_articles(wid)
        finally:
            store.close()
        self.assertTrue(any(int(r['id']) == self.article_id for r in rows))

    def test_add_to_watchlist_is_idempotent(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'
        research_tui.add_selected_to_watchlist(state)
        msg = research_tui.add_selected_to_watchlist(state)  # second press
        self.assertIn("Already in watchlist 'terminal'", msg)
        store = ResearchStore(self.db_path)
        try:
            wid = int(store.list_watchlists()[0]['id'])
            rows = store.list_watchlist_articles(wid)
        finally:
            store.close()
        self.assertEqual(sum(int(r['id']) == self.article_id for r in rows), 1)

    def test_remove_selected_article_from_watchlist(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'
        research_tui.add_selected_to_watchlist(state)
        # View the theme, then remove the study from it.
        state.active_tab = 'watchlist'
        state.focus = 'articles'
        research_tui.load_watchlist_articles(state)
        state.selected_article = 0
        msg = research_tui.remove_selected_from_watchlist(state)
        self.assertIn("Removed from watchlist 'terminal'", msg)
        self.assertFalse(any(int(r['id']) == self.article_id for r in state.watchlist_articles))
        store = ResearchStore(self.db_path)
        try:
            wid = int(store.list_watchlists()[0]['id'])
            self.assertEqual(store.list_watchlist_articles(wid), [])
            # the study itself is untouched — only the membership link is dropped
            self.assertIsNotNone(store.get_article(self.article_id))
        finally:
            store.close()

    def test_remove_when_absent_is_graceful(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'
        state.selected_article = 0
        msg = research_tui.remove_selected_from_watchlist(state)  # never added
        self.assertIn("Not in watchlist 'terminal'", msg)

    def test_watchlist_ops_without_any_watchlist_are_graceful(self):
        store = ResearchStore(self.db_path)
        try:
            store.conn.execute('DELETE FROM watchlists')
            store.conn.commit()
        finally:
            store.close()
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'saved'
        self.assertIn('No watchlist yet', research_tui.add_selected_to_watchlist(state))
        self.assertIn('No watchlist yet', research_tui.remove_selected_from_watchlist(state))

    def test_add_to_watchlist_without_article_is_graceful(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'current'   # no current results in the fixture
        state.selected_watchlist = 0
        self.assertIn('No article selected', research_tui.add_selected_to_watchlist(state))

    def test_add_fresh_search_result_to_watchlist_from_current_tab(self):
        # The literal ask: add an item to the watchlist straight from the search
        # results (the CURRENT tab), not from the saved library.
        state = research_tui.load_state(self.db_path, limit=20)

        def runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            return [research_tui.ResearchArticle(
                title='Fresh search hit',
                source='PubMed',
                date='2026-06-01',
                doi='10.1/fresh',
                abstract='randomized clinical trial n=60 safety efficacy outcome',
            )]

        research_tui.search_and_store_from_tui(state, 'fresh query', max_results=1, runner=runner)
        state.active_tab = 'current'
        state.selected_article = 0
        state.selected_watchlist = 0          # 'terminal' theme highlighted
        msg = research_tui.add_selected_to_watchlist(state)
        self.assertIn("Added to watchlist 'terminal'", msg)
        fresh_id = int(state.current_articles[0]['id'])
        store = ResearchStore(self.db_path)
        try:
            wid = int(store.list_watchlists()[0]['id'])
            rows = store.list_watchlist_articles(wid)
        finally:
            store.close()
        self.assertTrue(any(int(r['id']) == fresh_id for r in rows))

    # ── Topic (watchlist theme) management ───────────────────────────────────
    def test_add_watchlist_upsert_changes_subject_of_existing_topic(self):
        # The core bug: re-submitting a topic name used to be silently ignored,
        # so its subject (query) could never change. It must now update in place.
        store = ResearchStore(self.db_path)
        try:
            first = store.add_watchlist('terminal', 'terminal interface', ['pubmed'], 'fr')
            again = store.add_watchlist('terminal', 'NEW subject about quantum', ['pubmed', 'arxiv'], 'en')
            self.assertEqual(first, again)  # same row, not a duplicate
            row = store.get_watchlist('terminal')
            self.assertEqual(row['query'], 'NEW subject about quantum')
            self.assertEqual(row['lang'], 'en')
            self.assertEqual(len([w for w in store.list_watchlists() if w['name'] == 'terminal']), 1)
        finally:
            store.close()

    def test_update_watchlist_renames_and_changes_subject(self):
        store = ResearchStore(self.db_path)
        try:
            wid = int(store.get_watchlist('terminal')['id'])
            ok = store.update_watchlist(wid, name='renamed-topic', query='brand new subject')
            self.assertTrue(ok)
            self.assertIsNone(store.get_watchlist('terminal'))
            row = store.get_watchlist('renamed-topic')
            self.assertEqual(row['query'], 'brand new subject')
        finally:
            store.close()

    def test_delete_watchlist_removes_topic_and_hits_but_keeps_articles(self):
        store = ResearchStore(self.db_path)
        try:
            wid = int(store.get_watchlist('terminal')['id'])
            store.record_watchlist_hits(wid, [self.article_id])
            self.assertTrue(store.delete_watchlist(wid))
            self.assertIsNone(store.get_watchlist('terminal'))
            self.assertEqual(store.list_watchlist_articles(wid), [])
            self.assertIsNotNone(store.get_article(self.article_id))  # study kept
        finally:
            store.close()

    def test_edit_selected_topic_changes_subject_via_tui(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'watchlist'
        state.focus = 'watchlists'
        state.selected_watchlist = 0
        research_tui.begin_edit_selected_topic(state)
        self.assertEqual(state.prompt_action, 'edit_topic_name')
        self.assertNotEqual(state.editing_watchlist_id, 0)
        msg = research_tui.apply_topic_edit(state, 'terminal', 'totally new subject')
        self.assertIn('Topic updated', msg)
        store = ResearchStore(self.db_path)
        try:
            self.assertEqual(store.get_watchlist('terminal')['query'], 'totally new subject')
        finally:
            store.close()

    def test_edit_topic_rename_collision_is_graceful(self):
        store = ResearchStore(self.db_path)
        try:
            store.add_watchlist('other-topic', 'something else', ['pubmed'], 'fr')
            wid = int(store.get_watchlist('terminal')['id'])
        finally:
            store.close()
        state = research_tui.load_state(self.db_path, limit=20)
        state.editing_watchlist_id = wid
        msg = research_tui.apply_topic_edit(state, 'other-topic', 'x')  # name already used
        self.assertIn('Rename failed', msg)

    def test_delete_selected_topic_via_tui(self):
        state = research_tui.load_state(self.db_path, limit=20)
        state.active_tab = 'watchlist'
        state.focus = 'watchlists'
        state.selected_watchlist = 0
        msg = research_tui.delete_selected_topic(state)
        self.assertIn('Deleted topic: terminal', msg)
        self.assertFalse(any(w['name'] == 'terminal' for w in state.watchlists))

    def test_topic_edit_and_delete_without_any_topic_are_graceful(self):
        store = ResearchStore(self.db_path)
        try:
            store.conn.execute('DELETE FROM watchlists')
            store.conn.commit()
        finally:
            store.close()
        state = research_tui.load_state(self.db_path, limit=20)
        self.assertIn('No topic to edit', research_tui.begin_edit_selected_topic(state))
        self.assertIn('No topic to delete', research_tui.delete_selected_topic(state))


class SearchConfigTests(unittest.TestCase):
    def _state(self, **kw):
        return research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[], **kw)

    def test_resolve_maps_knobs_to_sources_deep_and_max(self):
        st = self._state(search_max=5, sensitivity_idx=2, depth_idx=0)
        cfg = research_tui.resolve_search_config(st)
        self.assertEqual(cfg['max'], 5)
        self.assertEqual(cfg['sources'], ['pubmed', 'crossref', 'openalex'])  # Balanced
        self.assertFalse(cfg['deep'])                                          # Overview
        # Deep path turns on at Deep+.
        st.depth_idx = 1
        self.assertTrue(research_tui.resolve_search_config(st)['deep'])

    def test_exhaustive_depth_widens_the_max_net(self):
        st = self._state(search_max=10, depth_idx=len(research_tui.DEPTH_LEVELS) - 1)
        cfg = research_tui.resolve_search_config(st)
        self.assertEqual(cfg['max'], 15)  # 10 * 1.5
        # …but never beyond the hard cap.
        st.search_max = research_tui.MAX_MAX
        self.assertEqual(research_tui.resolve_search_config(st)['max'], research_tui.MAX_MAX)

    def test_scihub_is_off_at_every_depth_by_default(self):
        # Sci-Hub is opt-in and decoupled from the depth knob: turning the depth
        # up must never enable it on its own (env unset → off).
        with mock.patch.dict('os.environ', {}, clear=False):
            import os as _os
            _os.environ.pop('DR_NEWPAPER_ALLOW_SCIHUB', None)
            for depth_idx in range(len(research_tui.DEPTH_LEVELS)):
                cfg = research_tui.resolve_search_config(self._state(depth_idx=depth_idx))
                self.assertFalse(cfg['allow_scihub'],
                                 f'Sci-Hub must stay off at depth_idx={depth_idx}')

    def test_scihub_honours_operator_opt_in(self):
        # An operator who opted in gets it at every depth — the TUI search config
        # propagates the choice rather than deriving it from the depth knob.
        with mock.patch.dict('os.environ', {'DR_NEWPAPER_ALLOW_SCIHUB': '1'}):
            for depth_idx in range(len(research_tui.DEPTH_LEVELS)):
                cfg = research_tui.resolve_search_config(self._state(depth_idx=depth_idx))
                self.assertTrue(cfg['allow_scihub'])

    def test_config_adjust_clamps_each_focused_knob(self):
        st = self._state(search_max=1, sensitivity_idx=0, depth_idx=0)
        st.config_focus = 0
        research_tui.config_adjust(st, -1)
        self.assertEqual(st.search_max, research_tui.MAX_MIN)  # clamped, no underflow
        research_tui.config_adjust(st, +1)
        self.assertEqual(st.search_max, research_tui.MAX_MIN + 1)
        st.config_focus = 1
        research_tui.config_adjust(st, -1)
        self.assertEqual(st.sensitivity_idx, 0)               # clamped at 0
        st.config_focus = 2
        for _ in range(10):
            research_tui.config_adjust(st, +1)
        self.assertEqual(st.depth_idx, len(research_tui.DEPTH_LEVELS) - 1)  # clamped at top
        st.config_focus = 3
        research_tui.config_adjust(st, -1)
        self.assertEqual(st.lang_idx, 0)                      # clamped at 0 (Français)
        for _ in range(10):
            research_tui.config_adjust(st, +1)
        self.assertEqual(st.lang_idx, len(research_tui.LANG_LEVELS) - 1)  # clamped at top (English)

    def test_resolve_search_config_defaults_lang_from_env(self):
        # No lang_idx override → the app-wide DR_NEWPAPER_LANG default.
        st = self._state()
        self.assertEqual(st.lang_idx, research_tui.DEFAULT_LANG_IDX)

    def test_resolve_search_config_exposes_chosen_lang(self):
        st = self._state(lang_idx=0)
        self.assertEqual(research_tui.resolve_search_config(st)['lang'], 'fr')
        st.lang_idx = 1
        self.assertEqual(research_tui.resolve_search_config(st)['lang'], 'en')

    def test_config_focus_cycles_through_four_search_knobs(self):
        st = self._state(config_focus=3)
        # Down from the last knob (language) wraps back to the first (max studies).
        research_tui.config_handle_key(st, curses.KEY_DOWN)
        self.assertEqual(st.config_focus, 0)
        # Up from the first knob wraps to language (index 3), not depth.
        research_tui.config_handle_key(st, curses.KEY_UP)
        self.assertEqual(st.config_focus, 3)


    def test_config_handle_key_navigates_adjusts_and_closes(self):
        st = self._state(config_focus=0)
        # Arrow down / vim j move focus.
        self.assertFalse(research_tui.config_handle_key(st, curses.KEY_DOWN))
        self.assertEqual(st.config_focus, 1)
        self.assertFalse(research_tui.config_handle_key(st, ord('j')))
        self.assertEqual(st.config_focus, 2)
        # Right adjusts the focused (depth) knob.
        st.depth_idx = 0
        research_tui.config_handle_key(st, curses.KEY_RIGHT)
        self.assertEqual(st.depth_idx, 1)
        # Enter and Esc both close.
        self.assertTrue(research_tui.config_handle_key(st, 27))
        self.assertTrue(research_tui.config_handle_key(st, 10))
        self.assertTrue(research_tui.config_handle_key(st, ord('o')))

    def test_gauge_shimmer_animates_between_frames(self):
        a = research_tui._config_gauge(0.6, 30, tick=0, focused=True)
        b = research_tui._config_gauge(0.6, 30, tick=3, focused=True)
        self.assertNotEqual(a, b)                       # shimmer cell moved
        self.assertIn('▒', a)
        # Unfocused gauge is steady (no shimmer).
        steady = research_tui._config_gauge(0.6, 30, tick=0, focused=False)
        self.assertNotIn('▒', steady)

    def test_config_demo_screen_lists_all_three_knobs(self):
        text = research_tui.render_config_demo(theme='matrix', frames=2)
        self.assertIn("Nombre maximum", text)
        self.assertIn("Sensibilité", text)
        self.assertIn("Profondeur", text)
        self.assertIn("Aperçu", text)

    def test_config_bars_and_value_chips_align_into_columns(self):
        # Every knob's gauge and its ⟨ value ⟩ chip must start at the same column
        # so the three bars read as aligned rows (the reported misalignment).
        st = self._state(search_max=8, sensitivity_idx=2, depth_idx=1)
        rows = [text for text, kind, _ in research_tui._config_lines(st, tick=0) if kind == 'gauge']
        self.assertEqual(len(rows), 3)                      # max + sensitivity + depth bars
        gauge_cols = {r.index('▕') for r in rows}           # gauge left cap
        chip_cols = {r.index('⟨') for r in rows}            # value chip start
        self.assertEqual(len(gauge_cols), 1, f"gauges not aligned: {gauge_cols}")
        self.assertEqual(len(chip_cols), 1, f"value chips not aligned: {chip_cols}")

    def test_explosion_ignites_at_origin_then_sweeps_to_every_border(self):
        cfg = research_tui.theme_config('bloomberg')
        core = cfg['sparkle']
        w, h = 84, 18
        origin = (12, 38)                                   # the depth cursor at max
        # Frame 0 is a single ignition glyph, exactly at the requested origin.
        g0 = research_tui.explosion_frames(0, w, h, 'bloomberg', origin)
        self.assertEqual(g0[12][38], core)
        self.assertEqual(''.join(g0).count(core), 1)
        # It visibly expands (a later frame differs and lights more cells).
        gmid = research_tui.explosion_frames(4, w, h, 'bloomberg', origin)
        self.assertNotEqual(g0, gmid)
        self.assertGreater(sum(ch != ' ' for row in gmid for ch in row),
                           sum(ch != ' ' for row in g0 for ch in row))
        # Over its whole span the wave lights every interior-border cell — it
        # really does propagate from the off-centre cursor to all four borders.
        span = research_tui.explosion_span(w, h, origin)
        lit = set()
        for f in range(span):
            for r, row in enumerate(research_tui.explosion_cells(f, w, h, 'bloomberg', origin)):
                for c, cell in enumerate(row):
                    if cell is not None:
                        lit.add((r, c))
        border = ({(0, c) for c in range(w)} | {(h - 1, c) for c in range(w)}
                  | {(r, 0) for r in range(h)} | {(r, w - 1) for r in range(h)})
        self.assertTrue(border <= lit, f"unreached border cells: {sorted(border - lit)[:6]}")
        # spent / degenerate cases
        self.assertEqual(research_tui.explosion_frames(span, w, h, 'bloomberg', origin), [])
        self.assertEqual(research_tui.explosion_frames(-1, 11, 7), [])
        self.assertEqual(research_tui.explosion_frames(0, 0, 7), [])

    def test_explosion_cells_carry_a_fading_tier_gradient(self):
        # The crest is tier 0 (brightest); tiers grow toward the ignition point so
        # the band reads as a fading shock-wave, and every tier indexes the colour
        # table painted in _draw_config.
        w, h = 60, 21
        origin = (10, 40)
        seen = set()
        for f in range(research_tui.explosion_span(w, h, origin)):
            for row in research_tui.explosion_cells(f, w, h, 'bloomberg', origin):
                for cell in row:
                    if cell is not None:
                        _glyph, tier = cell
                        self.assertTrue(0 <= tier < len(research_tui._EXPLOSION_TIER_ATTR))
                        seen.add(tier)
        self.assertEqual(seen, set(range(research_tui._EXPLOSION_TIERS)))  # all tiers occur

    def test_burst_origin_pins_to_the_depth_gauge_right_end(self):
        # "From the cursor at max": the ignition point is the right end of the
        # depth gauge's fill. Pin the concrete coordinate so a layout drift is loud.
        st = self._state(depth_idx=len(research_tui.DEPTH_LEVELS) - 1, config_focus=2)
        rows = research_tui._config_lines(st, 0)
        self.assertEqual(research_tui._config_burst_origin(rows), (12, 38))

    def test_deepest_depth_transition_arms_burst_but_repress_does_not(self):
        # Mirrors run_config_screen's detection predicate exactly.
        deepest = len(research_tui.DEPTH_LEVELS) - 1
        st = self._state(depth_idx=deepest - 1)
        st.config_focus = 2
        prev = st.depth_idx
        research_tui.config_handle_key(st, curses.KEY_RIGHT)
        self.assertTrue(st.depth_idx == deepest and prev != deepest)   # fires
        prev = st.depth_idx
        research_tui.config_handle_key(st, curses.KEY_RIGHT)           # already at top
        self.assertFalse(st.depth_idx == deepest and prev != deepest)  # does NOT re-fire

    def test_explosion_demo_renders_frames(self):
        out = research_tui.render_explosion_demo(theme='matrix')
        self.assertIn('Explosion demo', out)
        self.assertIn('frame', out)

    def test_live_config_loop_actually_draws_frame_zero_ignition(self):
        # Drives the REAL run_config_screen with a fake screen and records the
        # burst frame each _draw_config would paint. Guards the live tick
        # arithmetic (the adversarial review found frame 0 was being skipped).
        deepest = len(research_tui.DEPTH_LEVELS) - 1
        drawn_bursts = []

        def recorder(stdscr, state, tick):
            start = state.explosion_start_tick
            if start > -10**8:               # burst armed
                drawn_bursts.append(tick - start)

        class FakeScr:
            def __init__(self, keys):
                self.keys = list(keys)
            def timeout(self, *a):
                pass
            def getch(self):
                return self.keys.pop(0) if self.keys else 10   # Enter ends the loop

        # RIGHT crosses into the deepest rung, then timeouts advance the tick
        # through the burst, then Enter closes.
        n_frames = 10
        keys = [curses.KEY_RIGHT] + [-1] * n_frames + [10]
        state = self._state(depth_idx=deepest - 1)
        state.config_focus = 2               # focus the depth knob
        orig = research_tui._draw_config
        research_tui._draw_config = recorder
        try:
            research_tui.run_config_screen(FakeScr(keys), state, frame_ms=1)
        finally:
            research_tui._draw_config = orig

        self.assertEqual(state.depth_idx, deepest)
        self.assertIn(0, drawn_bursts, "frame-0 ignition was never drawn in the live loop")
        # and the burst counter advances one frame at a time from 0, in order
        self.assertEqual(drawn_bursts, list(range(len(drawn_bursts))))
        self.assertGreaterEqual(len(drawn_bursts), n_frames)

    # --- Meta-analysis config tab ---

    def test_resolve_meta_config_defaults(self):
        st = self._state()
        mc = research_tui.resolve_meta_config(st)
        self.assertEqual(mc['max'], 8)
        self.assertIn('pubmed', mc['sources'])
        self.assertIn('openalex', mc['sources'])

    def test_resolve_meta_config_knobs(self):
        st = self._state(meta_max_articles=15, meta_sources_idx=0)
        mc = research_tui.resolve_meta_config(st)
        self.assertEqual(mc['max'], 15)
        self.assertEqual(mc['sources'], ['pubmed', 'openalex'])  # Essential

    def test_meta_config_adjust_studies_and_sources(self):
        st = self._state()
        st.config_tab = 'meta'
        st.meta_max_articles = 8
        st.meta_sources_idx = 1
        st.config_focus = 0
        research_tui.config_adjust(st, +1)
        self.assertEqual(st.meta_max_articles, 9)
        research_tui.config_adjust(st, -1)
        self.assertEqual(st.meta_max_articles, 8)
        st.config_focus = 1
        research_tui.config_adjust(st, +1)
        self.assertEqual(st.meta_sources_idx, 2)
        # Clamping at both ends
        research_tui.config_adjust(st, +100)
        self.assertEqual(st.meta_sources_idx, len(research_tui.META_SOURCES_LEVELS) - 1)
        research_tui.config_adjust(st, -100)
        self.assertEqual(st.meta_sources_idx, 0)

    def test_meta_config_adjust_does_not_touch_search_knobs(self):
        st = self._state(search_max=5)
        st.config_tab = 'meta'
        st.config_focus = 0
        research_tui.config_adjust(st, +5)
        self.assertEqual(st.search_max, 5)   # search knob untouched

    def test_config_handle_key_tab_switches_and_resets_focus(self):
        st = self._state(config_focus=2)
        self.assertFalse(research_tui.config_handle_key(st, ord('\t')))
        self.assertEqual(st.config_tab, 'meta')
        self.assertEqual(st.config_focus, 0)  # focus reset on switch
        self.assertFalse(research_tui.config_handle_key(st, ord('\t')))
        self.assertEqual(st.config_tab, 'search')
        self.assertEqual(st.config_focus, 0)

    def test_meta_tab_focus_cycles_within_three_knobs(self):
        st = self._state(config_focus=0)
        st.config_tab = 'meta'
        research_tui.config_handle_key(st, curses.KEY_DOWN)
        self.assertEqual(st.config_focus, 1)
        research_tui.config_handle_key(st, curses.KEY_DOWN)
        self.assertEqual(st.config_focus, 2)
        research_tui.config_handle_key(st, curses.KEY_DOWN)
        self.assertEqual(st.config_focus, 0)  # wraps at META_CONFIG_FOCUS_COUNT=3

    def test_meta_config_lines_has_three_knobs(self):
        st = self._state()
        rows = research_tui._meta_config_lines(st, tick=0)
        labels = [text for text, kind, _ in rows if kind == 'label']
        self.assertEqual(len(labels), 3)
        combined = ' '.join(labels)
        self.assertIn('Number of studies', combined)
        self.assertIn('Reference databases', combined)
        self.assertIn('Analysis depth', combined)
        preview = next((text for text, kind, _ in rows if kind == 'preview'), "")
        self.assertIn('studies', preview)
        self.assertIn('depth', preview)

    def test_render_config_screen_shows_active_tab(self):
        st = self._state()
        lines = research_tui.render_config_screen(st, tick=0)
        self.assertIn('[ Search ]', lines[0])
        self.assertNotIn('[ Meta', lines[0])
        st.config_tab = 'meta'
        lines = research_tui.render_config_screen(st, tick=0)
        self.assertIn('[ Meta-Analysis ]', lines[0])
        self.assertNotIn('[ Search ]', lines[0])

    def test_meta_max_articles_clamps(self):
        st = self._state(meta_max_articles=research_tui.META_MAX_MAX)
        st.config_tab = 'meta'
        st.config_focus = 0
        research_tui.config_adjust(st, +100)
        self.assertEqual(st.meta_max_articles, research_tui.META_MAX_MAX)
        st.meta_max_articles = research_tui.META_MAX_MIN
        research_tui.config_adjust(st, -100)
        self.assertEqual(st.meta_max_articles, research_tui.META_MAX_MIN)


class FrontBlockTests(unittest.TestCase):
    def test_logo_has_a_dot_between_dr_and_np(self):
        rows = research_tui._logo_rows()
        self.assertEqual(len(rows), 6)
        # The 2x2 block dot lives on the two baseline rows, surrounded by spaces.
        dot_rows = [r for r in rows if ' ██ ' in r]
        self.assertEqual(len(dot_rows), 2)
        # And not on the upper rows (so it reads as a period, not a glyph mid-word).
        self.assertNotIn(' ██ ', rows[0])

    def test_front_block_lines_share_one_display_width(self):
        # Wide glyphs (📄) span two cells; the right border must still line up, so
        # every rendered line must have the SAME display width (not code-point len).
        for theme in research_tui.THEME_ORDER:
            block = research_tui.render_logo_block(theme, width=120)
            widths = {research_tui._disp_width(line) for line in block}
            self.assertEqual(widths, {120}, f"{theme} front is ragged: {widths}")

    def test_front_block_anchors_theme_with_tagline_companion_and_frame(self):
        for theme in research_tui.THEME_ORDER:
            cfg = research_tui.theme_config(theme)
            bs = research_tui.border_style(cfg.get('border'))
            block = research_tui.render_logo_block(theme, width=120)
            text = '\n'.join(block)
            self.assertIn(cfg['tagline'], text)            # themed tagline anchored on the front
            self.assertIn(cfg['mascot_name'], text)        # companion named on the front
            # Frame uses the theme's own border charset (round/double/heavy/ascii…).
            self.assertTrue(block[0].startswith(bs['tl']) and block[0].endswith(bs['tr']))
            self.assertTrue(block[-1].startswith(bs['bl']) and block[-1].endswith(bs['br']))

    def test_every_theme_defines_tagline_companion_border_and_font(self):
        for theme in research_tui.THEME_ORDER:
            cfg = research_tui.theme_config(theme)
            self.assertTrue(cfg.get('tagline'))
            self.assertIn(cfg.get('border'), research_tui.BORDER_STYLES)
            self.assertIn(cfg.get('logo_font'), research_tui.LOGO_FONTS)

    def test_all_six_companions_satisfy_constraints(self):
        for theme in research_tui.THEME_ORDER:
            mascot = research_tui.theme_config(theme)['mascot']
            self.assertEqual(len(mascot), 6, theme)
            self.assertTrue(all(14 <= len(line) <= 34 for line in mascot), theme)
            self.assertTrue(any('📄' in line for line in mascot), theme)
            joined = '\n'.join(mascot)
            self.assertTrue(any(c in joined for c in '╭╮╰╯╔╗╚╝'), theme)
            self.assertFalse(any(b in joined for b in ('/\\_/\\', '( o.o )', '> ^ <', '(\\_/)')), theme)


class SearchResultCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'research.db'

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_keeps_fresh_result_scoring_below_library_top_100(self):
        # Saturate the saved library with 110 high-scoring studies so any fresh
        # low-score result ranks outside the top-100 that load_state returns.
        store = ResearchStore(self.db_path)
        try:
            for i in range(110):
                aid = store.upsert_article(
                    {'title': f'High scoring seed {i}', 'doi': f'10.seed/{i}',
                     'abstract': 'systematic review meta-analysis randomized controlled trial'},
                    query='seed')
                store.upsert_score(aid, {
                    'novelty_score': 0, 'evidence_score': 0, 'citation_score': 0,
                    'clinical_relevance_score': 0, 'risk_score': 0,
                    'final_score': 95, 'label': 'HOT'})
        finally:
            store.close()
        state = research_tui.load_state(self.db_path, limit=100)

        def weak_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            return [research_tui.ResearchArticle(
                title='Fresh weak preprint', source='biorxiv', date='2026-06-01',
                doi='10.fresh/weak', abstract='preprint note')]

        research_tui.search_and_store_from_tui(state, 'weak topic', max_results=1, runner=weak_runner)
        titles = [r.get('title') for r in state.current_articles]
        # Regression: previously dropped because it fell outside the top-100 filter.
        self.assertIn('Fresh weak preprint', titles)

    def test_search_dedupes_same_doi_from_multiple_sources_in_count(self):
        state = research_tui.load_state(self.db_path, limit=20)

        def dup_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            return [
                research_tui.ResearchArticle(title='Dup study', source='pubmed', doi='10.dup/x', abstract='clinical trial'),
                research_tui.ResearchArticle(title='Dup study', source='openalex', doi='10.dup/x', abstract='clinical trial'),
            ]

        msg = research_tui.search_and_store_from_tui(state, 'dup', max_results=2, runner=dup_runner)
        self.assertEqual(len(state.current_articles), 1)
        self.assertIn('1 article stored', msg)


class SearchAnimationTests(unittest.TestCase):
    def test_pick_research_verb_is_a_seedable_verb(self):
        verb = research_tui.pick_research_verb(seed=1)
        self.assertIn(verb, research_tui.RESEARCH_VERBS)
        # Seed makes the choice deterministic so the animation is testable.
        self.assertEqual(verb, research_tui.pick_research_verb(seed=1))
        # And the pool is rich enough that different seeds can differ.
        seeds = {research_tui.pick_research_verb(seed=s) for s in range(40)}
        self.assertGreater(len(seeds), 3)

    def test_spinner_frame_cycles_within_theme_specific_frames(self):
        frames = research_tui.theme_spinner_frames('cute')
        self.assertTrue(frames)
        self.assertEqual(research_tui.spinner_frame('cute', 0), frames[0])
        # Wraps modulo the number of frames.
        self.assertEqual(research_tui.spinner_frame('cute', len(frames)), frames[0])
        self.assertEqual(research_tui.spinner_frame('cute', len(frames) + 1), frames[1])
        # Themes use distinct spinner glyphs.
        self.assertNotEqual(research_tui.theme_spinner_frames('matrix'),
                            research_tui.theme_spinner_frames('cute'))

    def test_spinner_trail_keeps_exactly_one_marker_and_bounces(self):
        positions = []
        for tick in range(14):
            trail = research_tui.spinner_trail(tick, width=5)
            self.assertEqual(len(trail), 5)
            self.assertEqual(trail.count('●'), 1)
            positions.append(trail.index('●'))
        # Beam reaches both edges over a full cycle.
        self.assertIn(0, positions)
        self.assertIn(4, positions)

    def test_format_search_animation_shows_verb_query_and_elapsed(self):
        text = research_tui.format_search_animation('Investigating', 'oral minoxidil', theme='bloomberg', tick=2, elapsed=3.7)
        self.assertIn('Investigating', text)
        self.assertIn('oral minoxidil', text)
        self.assertIn('3s', text)  # elapsed floored to seconds
        self.assertIn('searching', text)
        # Consecutive ticks animate (different spinner glyph) -> the loop visibly moves.
        a = research_tui.format_search_animation('Investigating', 'q', tick=0)
        b = research_tui.format_search_animation('Investigating', 'q', tick=1)
        self.assertNotEqual(a, b)

    def test_run_with_spinner_animates_across_frames_and_returns_value(self):
        state = research_tui.TuiState(db_path=Path('x'), articles=[], watchlists=[])
        ticks = []
        release = threading.Event()

        def work():
            release.wait(2.0)
            return 'DONE'

        def fake_draw(scr, st):
            ticks.append(st.spinner_tick)
            if len(ticks) >= 3:
                release.set()

        class FakeScreen:
            def nodelay(self, flag):
                pass

            def getch(self):
                return -1

        out = research_tui.run_with_spinner(
            FakeScreen(), state, 'Investigating', work, query='x',
            draw=fake_draw, nap=lambda ms: None)

        self.assertEqual(out, 'DONE')
        self.assertFalse(state.busy)
        self.assertEqual(state.spinner_verb, 'Investigating')
        self.assertGreaterEqual(len(ticks), 2)
        # spinner_tick strictly advanced frame to frame -> animation actually ran.
        self.assertTrue(all(b > a for a, b in zip(ticks, ticks[1:])))
        # And those ticks would paint different frames.
        self.assertNotEqual(
            research_tui.format_search_animation('Investigating', 'x', tick=ticks[0]),
            research_tui.format_search_animation('Investigating', 'x', tick=ticks[1]))

    def test_run_with_spinner_propagates_worker_error_and_clears_busy(self):
        state = research_tui.TuiState(db_path=Path('x'), articles=[], watchlists=[])

        def boom():
            raise RuntimeError('provider exploded')

        class FakeScreen:
            def nodelay(self, flag):
                pass

            def getch(self):
                return -1

        with self.assertRaises(RuntimeError):
            research_tui.run_with_spinner(FakeScreen(), state, 'Wrangling', boom,
                                          draw=lambda scr, st: None, nap=lambda ms: None)
        self.assertFalse(state.busy)

    def test_spin_demo_renders_animated_frames_for_each_theme(self):
        for theme in ('bloomberg', 'matrix', 'cute'):
            dump = research_tui.render_spin_demo(theme=theme, frames=6)
            self.assertIn('Spinner demo', dump)
            self.assertIn('searching', dump)
            # The sweeping progress bar is present.
            self.assertIn('█', dump)


class BackgroundTaskTests(unittest.TestCase):
    """Non-blocking background search + companion completion toast (objectives ③/④)."""

    def _state(self):
        return research_tui.TuiState(db_path=Path('x'), articles=[], watchlists=[])

    def _task(self, **kw):
        defaults = dict(kind='search', verb='Investigating', query='q',
                        worker=lambda: None,
                        apply=lambda st, res: 'found 3 studies for q')
        defaults.update(kw)
        return research_tui.BackgroundTask(**defaults)

    def test_poll_applies_result_then_companion_announces_and_expires(self):
        state = self._state()
        state.task = self._task(done=True, result='ignored', started=100.0)
        state.busy = True
        self.assertTrue(research_tui.poll_background_task(state, now=101.0))
        self.assertFalse(state.busy)            # the worker is done — UI is free again
        self.assertIsNone(state.task)
        self.assertEqual(state.notif_kind, 'success')
        self.assertIn('found 3 studies', state.notif_text)
        toast = research_tui.companion_notification(state, now=102.0)
        self.assertIsNotNone(toast)
        self.assertIn('found 3 studies', toast)
        # the active theme's companion announces it, staying in character
        self.assertIn(research_tui.theme_config(state.theme)['mascot_name'], toast)
        # and the toast clears once its TTL elapses
        self.assertIsNone(research_tui.companion_notification(
            state, now=101.0 + research_tui.NOTIF_TTL + 0.1))

    def test_poll_surfaces_worker_error_as_companion_error(self):
        state = self._state()
        state.task = self._task(done=True, error=RuntimeError('provider exploded'), started=5.0)
        state.busy = True
        self.assertTrue(research_tui.poll_background_task(state, now=6.0))
        self.assertFalse(state.busy)
        self.assertEqual(state.notif_kind, 'error')
        self.assertIn('failed', state.notif_text)
        self.assertIn('provider exploded', state.notif_text)

    def test_poll_is_noop_while_worker_runs_but_keeps_elapsed_fresh(self):
        state = self._state()
        state.task = self._task(done=False, started=1.0)
        state.busy = True
        self.assertFalse(research_tui.poll_background_task(state, now=3.0))
        self.assertTrue(state.busy)             # still running — stays busy
        self.assertIsNotNone(state.task)
        self.assertEqual(state.spinner_elapsed, 2.0)  # spinner clock kept fresh

    def test_poll_with_no_task_is_false(self):
        self.assertFalse(research_tui.poll_background_task(self._state(), now=1.0))


class EscapeSequenceTests(unittest.TestCase):
    """Mid-search navigation: arrow-key escape sequences must not read as quit.

    During a background search the input loop runs non-blocking, so curses hands
    back a raw ESC and _resolve_escape must reassemble the arrow. Regression: a
    split sequence (continuation bytes not yet in the buffer) used to resolve to
    a bare ESC (27), which the main loop treats as quit — killing the worker so
    the search vanished with no result and no companion announcement.
    """

    class _FakeScr:
        def __init__(self, reads):
            self.reads = list(reads)
            self.timeouts = []

        def timeout(self, ms):
            self.timeouts.append(ms)

        def getch(self):
            return self.reads.pop(0) if self.reads else -1

    def test_split_arrow_sequence_resolves_to_arrow_not_quit(self):
        # ESC already consumed by the loop; '[' 'A' arrive only after a stall.
        scr = self._FakeScr([-1, -1, ord('['), ord('A')])
        self.assertEqual(research_tui._resolve_escape(scr), curses.KEY_UP)
        # the peek must be a brief *blocking* wait, never the racy timeout(0)
        self.assertTrue(all(ms > 0 for ms in scr.timeouts))

    def test_contiguous_arrow_sequence_resolves(self):
        for final, expected in ((ord('A'), curses.KEY_UP), (ord('B'), curses.KEY_DOWN),
                                (ord('C'), curses.KEY_RIGHT), (ord('D'), curses.KEY_LEFT)):
            scr = self._FakeScr([ord('['), final])
            self.assertEqual(research_tui._resolve_escape(scr), expected)

    def test_ss3_arrow_sequence_resolves(self):
        scr = self._FakeScr([ord('O'), ord('C')])  # SS3 form (application keypad)
        self.assertEqual(research_tui._resolve_escape(scr), curses.KEY_RIGHT)

    def test_genuine_lone_escape_stays_escape(self):
        scr = self._FakeScr([])  # nothing follows within the window
        self.assertEqual(research_tui._resolve_escape(scr), 27)

    def test_escape_then_ordinary_key_is_lone_escape(self):
        scr = self._FakeScr([ord('s')])  # ESC then a normal keypress
        self.assertEqual(research_tui._resolve_escape(scr), 27)


class ProgressPanelTests(unittest.TestCase):
    """Staged 'ultracode'-style progress channel + panel (deep-search animation)."""

    def test_channel_retires_finished_stages_to_history(self):
        chan = research_tui.ProgressChannel()
        chan.report('sources', 'PubMed')
        chan.report('dedup', '4 unique', 4, 4)
        chan.report('process', 'PDF', 1, 4)
        snap = chan.snapshot()
        self.assertEqual(snap['stage'], 'process')
        self.assertEqual(snap['current'], 1)
        self.assertEqual(snap['total'], 4)
        self.assertEqual(snap['detail'], 'PDF')
        # earlier stages are retired, in order, exactly once each
        self.assertEqual(snap['history'], ['sources', 'dedup'])

    def test_panel_marks_done_active_and_pending_stages(self):
        chan = research_tui.ProgressChannel()
        chan.report('sources', 'PubMed')
        chan.report('process', 'AI synthesis · trial', 2, 5)
        lines = research_tui.format_progress_panel(chan.snapshot(), 'bloomberg', tick=3, elapsed=7.0)
        body = '\n'.join(lines)
        self.assertIn('7s elapsed', body)
        self.assertIn('✓ Search the literature', body)        # done stage
        self.assertIn('2/5', body)                            # active per-item counter
        self.assertIn('AI synthesis · trial', body)           # active detail
        self.assertIn('· Score & evaluate', body)             # pending stage
        # the active row is NOT a checkmark and NOT a dim dot
        active_row = next(l for l in lines if 'Read & summarize' in l)
        self.assertNotIn('✓', active_row)

    def test_panel_animates_between_frames(self):
        chan = research_tui.ProgressChannel()
        chan.report('process', 'PDF', 1, 5)
        snap = chan.snapshot()
        f0 = research_tui.format_progress_panel(snap, 'matrix', tick=0)
        f1 = research_tui.format_progress_panel(snap, 'matrix', tick=1)
        self.assertNotEqual(f0, f1)  # spinner glyph / scan bar advance

    def test_progress_demo_renders_full_checklist(self):
        dump = research_tui.render_progress_demo(theme='cute')
        self.assertIn('Progress demo', dump)
        self.assertIn('Search the literature', dump)
        self.assertIn('Score & evaluate', dump)
        self.assertIn('✓', dump)  # at least one completed stage appears

    def test_search_and_store_forwards_progress_then_reports_evaluate(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / 'p.db'
        ResearchStore(db_path).close()
        chan = research_tui.ProgressChannel()
        seen = []

        def runner(query, max_results, lang, sources, deep=False, allow_scihub=False, progress_cb=None):
            # the deep runner must receive the progress callback…
            self.assertIsNotNone(progress_cb)
            progress_cb('sources', 'PubMed', 0, 0)
            seen.append('runner')
            return [research_tui.ResearchArticle(title='A study', source='PubMed', doi='10.1/x',
                                                 abstract='trial n=10')]

        state = research_tui.load_state(db_path, limit=10)
        research_tui.search_and_store_from_tui(
            state, 'q', max_results=1, sources=['pubmed'], deep=True,
            runner=runner, progress_cb=chan.report)
        self.assertEqual(seen, ['runner'])
        snap = chan.snapshot()
        # …and the store loop ends on the 'evaluate' stage
        self.assertEqual(snap['stage'], 'evaluate')
        self.assertIn('sources', snap['history'])

    def test_search_and_store_tolerates_runner_without_progress_cb(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / 'p.db'
        ResearchStore(db_path).close()

        def legacy_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            return [research_tui.ResearchArticle(title='Legacy', source='PubMed', doi='10.1/l',
                                                 abstract='trial')]

        state = research_tui.load_state(db_path, limit=10)
        # Must not raise even though the runner has no progress_cb parameter.
        msg = research_tui.search_and_store_from_tui(
            state, 'q', max_results=1, sources=['pubmed'],
            runner=legacy_runner, progress_cb=research_tui.ProgressChannel().report)
        self.assertIn('1 article', msg)

    def test_make_search_task_attaches_progress_channel(self):
        state = research_tui.TuiState(db_path=Path('x'), articles=[], watchlists=[])
        task = research_tui.make_search_task(state, 'q', verb='Investigating', max_results=1)
        self.assertIsInstance(task.progress, research_tui.ProgressChannel)

    def test_progress_band_is_compact_with_stages_findings_and_reaction(self):
        chan = research_tui.ProgressChannel()
        chan.report('sources', 'PubMed')
        chan.report('process', 'PDF', 2, 5)
        chan.add_result('Oral minoxidil RCT', 84, 'HOT', 'Quill Terminal: strong')
        lines = research_tui.format_progress_band(chan.snapshot(), 'bloomberg', tick=3, elapsed=7.0, width=100)
        self.assertLessEqual(len(lines), 4)                         # stays a thin band
        self.assertLessEqual(len(lines), research_tui.PROGRESS_BAND_H - 2)  # fits inside the bordered band
        body = '\n'.join(lines)
        self.assertIn('7s', body)                                  # elapsed
        self.assertIn('✓ Search', body)                            # a done stage chip
        self.assertIn('2/5', body)                                 # active per-item counter
        self.assertIn('Oral minoxidil RCT', body)                  # streamed finding
        self.assertIn('strong', body)                              # companion reaction
        self.assertTrue(all(len(l) <= 100 for l in lines))         # width-clipped

    def test_progress_band_handles_no_findings_yet(self):
        chan = research_tui.ProgressChannel()
        chan.report('sources', 'PubMed')
        lines = research_tui.format_progress_band(chan.snapshot(), 'bloomberg', tick=0, elapsed=0.0)
        self.assertIn('scanning sources', '\n'.join(lines))

    def test_progress_band_height_never_overlaps_boxes_or_is_suppressed_when_short(self):
        top = 14  # typical header height
        self.assertEqual(research_tui.progress_band_height(40, top, False), 0)   # not searching
        # Tall terminal: band reserved AND boxes (work_h) end strictly above the band.
        for h in (34, 40, 60):
            band_h = research_tui.progress_band_height(h, top, True)
            self.assertEqual(band_h, research_tui.PROGRESS_BAND_H)
            work_h = max(research_tui.MIN_WORK_H, h - top - 4 - band_h)
            boxes_last_row = top + work_h - 2          # bottom of the WATCHLISTS box
            band_top = h - 2 - band_h
            self.assertLess(boxes_last_row, band_top)  # no overlap
        # Minimal terminal (documented floor h=26): band suppressed -> no clamp collision.
        self.assertEqual(research_tui.progress_band_height(26, top, True), 0)
        self.assertEqual(research_tui.progress_band_height(33, top, True), 0)   # just under the threshold

    def test_progress_pane_only_takes_over_once_stages_are_reported(self):
        # The meta regression: a task attaches a ProgressChannel it never writes,
        # so the detail pane must NOT flip to a frozen, mislabeled search checklist.
        state = research_tui.TuiState(db_path=Path('x'), articles=[], watchlists=[])
        self.assertIsNone(research_tui.search_progress_snapshot(state))   # not busy
        chan = research_tui.ProgressChannel()
        task = research_tui.BackgroundTask(kind='meta', verb='Meta-analyzing', query='q',
                                           worker=lambda: None, apply=lambda l, r: 'done',
                                           progress=chan)
        state.task = task
        state.busy = True
        self.assertIsNone(research_tui.search_progress_snapshot(state))   # channel empty -> no takeover
        chan.report('sources', 'PubMed')                                  # now a stage is reported
        self.assertIsNotNone(research_tui.search_progress_snapshot(state))

    def test_legacy_runner_invoked_exactly_once(self):
        # Regression: signature inspection (not except TypeError) decides whether
        # to pass progress_cb, so a legacy runner must run once — never twice.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / 'p.db'
        ResearchStore(db_path).close()
        calls = []

        def legacy_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            calls.append(1)
            return [research_tui.ResearchArticle(title='Once', source='PubMed', doi='10.1/o', abstract='t')]

        state = research_tui.load_state(db_path, limit=10)
        research_tui.search_and_store_from_tui(
            state, 'q', max_results=1, runner=legacy_runner,
            progress_cb=research_tui.ProgressChannel().report)
        self.assertEqual(len(calls), 1)

    def test_internal_typeerror_in_progress_runner_propagates(self):
        # Regression: a TypeError raised *inside* a progress-aware runner must
        # surface (so the background task reports the error) rather than being
        # swallowed and the whole search silently re-run.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / 'p.db'
        ResearchStore(db_path).close()

        def boom_runner(query, max_results, lang, sources, deep=False, allow_scihub=False, progress_cb=None):
            raise TypeError('genuine bug deep inside the pipeline')

        state = research_tui.load_state(db_path, limit=10)
        with self.assertRaises(TypeError):
            research_tui.search_and_store_from_tui(
                state, 'q', max_results=1, runner=boom_runner,
                progress_cb=research_tui.ProgressChannel().report)

    def test_runner_accepts_progress_cb_detects_signature(self):
        self.assertTrue(research_tui._runner_accepts_progress_cb(
            lambda q, m, l, s, deep=False, allow_scihub=False, progress_cb=None: None))
        self.assertTrue(research_tui._runner_accepts_progress_cb(lambda *a, **k: None))
        self.assertFalse(research_tui._runner_accepts_progress_cb(
            lambda q, m, l, s, deep=False, allow_scihub=False: None))


class LiveResultsAndCompanionTests(unittest.TestCase):
    """Streaming findings + rotating query word + score-driven companion remarks."""

    def test_score_band_buckets_by_label_then_score(self):
        self.assertEqual(research_tui.score_band('HOT', 50), 'hot')
        self.assertEqual(research_tui.score_band('', 75), 'hot')
        self.assertEqual(research_tui.score_band('RISK', 90), 'weak')
        self.assertEqual(research_tui.score_band('', 30), 'weak')
        self.assertEqual(research_tui.score_band('NEW', 55), 'solid')

    def test_companion_reaction_is_in_character_and_band_appropriate(self):
        hot = research_tui.companion_reaction('matrix', 'HOT', 88)
        weak = research_tui.companion_reaction('matrix', 'RISK', 20)
        self.assertIn('Monolith Lynx', hot)      # the active theme's companion speaks
        self.assertIn('Monolith Lynx', weak)
        self.assertNotEqual(hot, weak)           # enthusiastic vs wary differ
        self.assertTrue(any(w in hot.lower() for w in ('strong', 'excellent', 'top')))
        self.assertTrue(any(w in weak.lower() for w in ('weak', 'careful', 'wary')))
        # deterministic in the score so redraws don't flicker the wording
        self.assertEqual(hot, research_tui.companion_reaction('matrix', 'HOT', 88))

    def test_rotating_query_term_cycles_significant_words_and_skips_stopwords(self):
        q = 'oral minoxidil for the alopecia'
        terms = {research_tui.rotating_query_term(q, t, period=2) for t in range(0, 8)}
        self.assertEqual(terms, {'oral', 'minoxidil', 'alopecia'})  # 'for'/'the' skipped
        self.assertEqual(research_tui.rotating_query_term('', 3), '')
        # advances over time
        self.assertNotEqual(research_tui.rotating_query_term(q, 0, period=2),
                            research_tui.rotating_query_term(q, 2, period=2))

    def test_format_search_animation_includes_rotating_focus_term(self):
        line = research_tui.format_search_animation('Scanning', 'crispr gene editing', tick=0)
        self.assertIn('crispr gene editing', line)   # full query still shown (back-compat)
        self.assertIn('⌕', line)                      # rotating focus marker present

    def test_progress_channel_streams_results_and_latest_reaction(self):
        chan = research_tui.ProgressChannel()
        chan.add_result('A strong RCT', 82, 'HOT', 'Quill Terminal: great')
        chan.add_result('A weak preprint', 25, 'RISK', 'Quill Terminal: careful')
        snap = chan.snapshot()
        self.assertEqual([r['score'] for r in snap['results']], [82, 25])
        self.assertEqual(snap['reaction'], 'Quill Terminal: careful')  # latest find
        panel = '\n'.join(research_tui.format_progress_panel(snap, 'bloomberg', tick=1, elapsed=2.0))
        self.assertIn('findings (2)', panel)
        self.assertIn('A strong RCT', panel)
        self.assertIn('careful', panel)               # companion remark rendered

    def test_search_and_store_streams_each_scored_study_to_result_cb(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / 'r.db'
        ResearchStore(db).close()
        state = research_tui.load_state(db)
        state.theme = 'bloomberg'

        def runner(q, mx, lang, src, deep=False, allow_scihub=False):
            return [
                research_tui.ResearchArticle(
                    title='Randomized controlled trial of X', source='PubMed', doi='10.1/a',
                    abstract='human randomized controlled trial n=300 clinical outcome', date='2026-05-01'),
                research_tui.ResearchArticle(
                    title='Tiny preprint', source='medRxiv', type='preprint', doi='10.1/b',
                    abstract='', date='2019'),
            ]

        chan = research_tui.ProgressChannel()
        research_tui.search_and_store_from_tui(
            state, 'oral minoxidil', max_results=2, runner=runner, result_cb=chan.add_result)
        snap = chan.snapshot()
        self.assertEqual(len(snap['results']), 2)                    # one per study, streamed
        self.assertTrue(any(r['label'] == 'HOT' or r['score'] >= 70 for r in snap['results']))
        self.assertTrue(snap['reaction'])                           # companion reacted


class CompanionVisibilityTests(unittest.TestCase):
    """The companion must REACH THE RENDERED FRAME in normal browsing — not just
    return a string from a pure function. These go through render_demo, the same
    path the user sees, so an invisible companion fails here (it didn't before)."""

    def _demo_for(self, article):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / 'c.db'
        store = ResearchStore(db)
        aid = store.upsert_article(article, query='x')
        store.upsert_score(aid, scoring.score_article(store.get_article(aid)))
        store.close()
        return research_tui.render_demo(db, width=120, theme='bloomberg')

    def test_companion_line_is_visible_in_rendered_demo_and_reacts_to_score(self):
        hot = self._demo_for({'title': 'Randomized controlled trial of X', 'type': 'randomized trial',
                              'date': '2026-06-01', 'doi': '10.1/h',
                              'abstract': 'human randomized controlled trial n=300 clinical outcome'})
        risk = self._demo_for({'title': 'Tiny medRxiv preprint', 'source': 'medRxiv', 'type': 'preprint',
                               'date': '2019', 'doi': '10.1/r', 'abstract': ''})
        hot_row = next(l for l in hot.splitlines() if l.startswith('COMPANION'))
        risk_row = next(l for l in risk.splitlines() if l.startswith('COMPANION'))
        # The companion is PRESENT in the rendered frame …
        self.assertIn('Quill Terminal', hot_row)
        # … and its remark adapts to the study's score band.
        self.assertTrue(any(w in hot_row.lower() for w in ('strong', 'excellent', 'top')))
        self.assertTrue(any(w in risk_row.lower() for w in ('weak', 'careful', 'wary')))
        self.assertNotEqual(hot_row, risk_row)

    def test_companion_line_reacts_to_the_selected_study_live(self):
        # The live browse path: companion_line keys off selected_article, so moving
        # the selection changes the remark (this is the "react to clicks" behaviour).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / 'c.db'
        store = ResearchStore(db)
        store.close()
        state = research_tui.load_state(db)
        state.active_tab = 'current'
        state.focus = 'articles'
        state.current_articles = [
            {'id': 1, 'title': 'Strong RCT', 'final_score': 86, 'label': 'HOT'},
            {'id': 2, 'title': 'Weak preprint', 'final_score': 20, 'label': 'RISK'},
        ]
        state.selected_article = 0
        first = research_tui.companion_line(state)
        state.selected_article = 1
        second = research_tui.companion_line(state)
        self.assertTrue(any(w in first.lower() for w in ('strong', 'excellent', 'top')))
        self.assertTrue(any(w in second.lower() for w in ('weak', 'careful', 'wary')))
        self.assertNotEqual(first, second)

    def test_companion_line_falls_back_to_ambient_when_empty(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / 'c.db'
        ResearchStore(db).close()
        state = research_tui.load_state(db)
        state.current_articles = []
        state.saved_articles = []
        line = research_tui.companion_line(state)
        self.assertIn('press s to search', line.lower())


class CompanionBubbleTests(unittest.TestCase):
    """Top-right speech bubble: per-companion English banter that praises good
    studies and roasts weak ones, reacting to the selected study."""

    def test_quip_praises_good_and_roasts_weak_per_companion(self):
        # Every companion has a distinct voice for both bands, in English.
        for theme in research_tui.THEME_ORDER:
            name = research_tui.theme_config(theme)['mascot_name']
            good = research_tui.companion_quip(theme, 'HOT', 88)
            bad = research_tui.companion_quip(theme, 'RISK', 15)
            self.assertTrue(good and bad, name)
            self.assertNotEqual(good, bad, name)            # praise != roast
        # Distinct companions give distinct lines (per-companion, not generic).
        self.assertNotEqual(research_tui.companion_quip('bloomberg', 'HOT', 88),
                            research_tui.companion_quip('cute', 'HOT', 88))

    def test_quip_is_deterministic_in_score(self):
        self.assertEqual(research_tui.companion_quip('matrix', 'HOT', 88),
                         research_tui.companion_quip('matrix', 'HOT', 88))

    def test_quip_handles_unknown_theme_gracefully(self):
        # An unknown theme resolves to the default companion and still yields a
        # non-empty, band-appropriate quip (never crashes / never blank).
        q = research_tui.companion_quip('does-not-exist', 'RISK', 10)
        self.assertTrue(q)
        bloomberg_weak = research_tui.COMPANION_QUIPS['Quill Terminal']['weak']
        self.assertIn(q, bloomberg_weak)   # default theme = bloomberg = Quill Terminal

    def test_bubble_lines_empty_without_a_study(self):
        self.assertEqual(research_tui.companion_bubble_lines('bloomberg', None), [])

    def test_bubble_reaches_rendered_demo_and_reacts_to_score(self):
        # Goes through render_demo (the path the user sees), not just the pure fn.
        def demo_bubble(article, theme='sepia'):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            db = Path(tmp.name) / 'b.db'
            store = ResearchStore(db)
            aid = store.upsert_article(article, query='x')
            store.upsert_score(aid, scoring.score_article(store.get_article(aid)))
            store.close()
            text = research_tui.render_demo(db, width=120, theme=theme)
            return next(l for l in text.splitlines() if l.startswith('BUBBLE'))

        hot = demo_bubble({'title': 'RCT of X', 'type': 'randomized trial', 'date': '2026-06-01',
                           'doi': '10.1/h', 'abstract': 'human randomized controlled trial n=300 clinical outcome'})
        risk = demo_bubble({'title': 'Tiny preprint', 'source': 'medRxiv', 'type': 'preprint',
                            'date': '2019', 'doi': '10.1/r', 'abstract': ''})
        self.assertIn('Scriba Owl', hot)                    # the companion is named in the bubble
        self.assertIn('rigorous', hot.lower())              # praise for the strong study
        self.assertIn('fire', risk.lower())                 # roast for the weak one
        self.assertNotEqual(hot, risk)


class CompanionBubbleLayoutTests(unittest.TestCase):
    """The pop-up bubble is anchored to the LEFT of the header companion with a
    tail pointing back at it, so a quip reads as coming from the companion."""

    class _Grid:
        def __init__(self, h, w):
            self.h, self.w = h, w
            self.cells = [[' '] * w for _ in range(h)]
        def getmaxyx(self):
            return (self.h, self.w)
        def addstr(self, y, x, text, attr=0):
            for i, ch in enumerate(text):
                if 0 <= y < self.h and 0 <= x + i < self.w:
                    self.cells[y][x + i] = ch
        def erase(self):
            pass
        def refresh(self):
            pass

    def _draw(self, theme='bloomberg', w=160):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[])
        st.theme = theme
        st.popup_text = 'Solid pick — well-powered RCT.'
        st.popup_band = 'hot'
        st.popup_until = 10 ** 18          # far-future so companion_popup shows it
        scr = self._Grid(30, w)
        orig = curses.color_pair                # color_pair needs initscr(); stub it headless
        curses.color_pair = lambda n: 0
        try:
            research_tui._draw_companion_bubble(scr, st, research_tui.theme_config(theme), w)
        finally:
            curses.color_pair = orig
        return scr

    def test_anchor_matches_front_spec_placement(self):
        # The bubble must pin to wherever the header actually paints the companion.
        for w in (96, 110, 120):
            ax, _top, cw = research_tui._companion_anchor('bloomberg', w)
            embs = [e for (_l, _r, e) in research_tui._front_spec('bloomberg', width=w) if e]
            self.assertTrue(embs)
            self.assertEqual(ax, embs[0][0])                # same column the header uses
            self.assertEqual(cw, max(research_tui._disp_width(e[1]) for e in embs))

    def test_bubble_sits_right_of_companion_without_touching_the_title(self):
        # Wide enough that the bubble fits in the free space right of the companion.
        emblem_x, _top, _cw = research_tui._companion_anchor('bloomberg', 160)
        scr = self._draw('bloomberg', 160)
        sparkle = research_tui.theme_config('bloomberg')['sparkle']
        # The entire bubble box is strictly RIGHT of the companion (so it can never
        # land on the left-hand wordmark/title).
        box_cols = [x for row in scr.cells for x, ch in enumerate(row) if ch in '╔╗╚╝═║']
        self.assertTrue(box_cols, "bubble box was not drawn")
        self.assertGreater(min(box_cols), emblem_x)
        # A tail sparkle bridges the gap between the companion and the bubble's left
        # edge — that is what makes the quip read as coming from the companion.
        tail_xs = [x for row in scr.cells for x, ch in enumerate(row) if ch == sparkle]
        self.assertTrue(any(emblem_x < x < min(box_cols) for x in tail_xs),
                        "no tail glyph bridging the companion to the bubble")

    def test_bubble_is_skipped_when_no_room_right_of_the_companion(self):
        # On a terminal too narrow to fit the bubble right of the companion it is
        # skipped (the always-on companion_line still covers commentary) — and it
        # is NEVER drawn over the left-hand title.
        scr = self._draw('bloomberg', 118)
        box_cols = [x for row in scr.cells for x, ch in enumerate(row) if ch in '╔╗╚╝═║']
        self.assertEqual(box_cols, [])


class CompanionPopupTests(unittest.TestCase):
    """Event-driven companion pop-ups: appear on selection / idle / long search /
    search done, live <=20s, and a new one interrupts the previous."""

    def _state_with_two_studies(self):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[], theme='bloomberg')
        st.active_tab = 'current'
        st.focus = 'articles'
        st.current_articles = [
            {'id': 1, 'title': 'Strong RCT', 'final_score': 88, 'label': 'HOT'},
            {'id': 2, 'title': 'Weak preprint', 'final_score': 18, 'label': 'RISK'},
        ]
        return st

    def test_popup_expires_after_at_most_20s_and_is_capped(self):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[])
        research_tui.push_companion_popup(st, 'hi', 'hot', now=100.0, ttl=999)  # request > cap
        self.assertEqual(st.popup_until, 100.0 + research_tui.POPUP_TTL)        # clamped to 20s
        self.assertEqual(research_tui.companion_popup(st, now=119.9), 'hi')
        self.assertIsNone(research_tui.companion_popup(st, now=120.1))          # expired

    def test_new_popup_interrupts_the_previous(self):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[])
        research_tui.push_companion_popup(st, 'first', 'solid', now=10.0)
        research_tui.push_companion_popup(st, 'second', 'weak', now=12.0)
        self.assertEqual(research_tui.companion_popup(st, now=12.1), 'second')
        self.assertEqual(st.popup_until, 12.0 + research_tui.POPUP_TTL)         # countdown reset

    def test_selecting_a_study_pops_a_reaction_and_changing_selection_interrupts(self):
        st = self._state_with_two_studies()
        research_tui.note_interaction(st, now=0.0)
        st.popup_selection_id = research_tui._selected_study_id(st)             # startup init (id 1)
        st.selected_article = 1                                                 # "click" study 2
        research_tui.update_companion_popups(st, now=0.1)
        weak_pop = research_tui.companion_popup(st, now=0.1)
        self.assertIsNotNone(weak_pop)
        st.selected_article = 0                                                 # click study 1
        research_tui.update_companion_popups(st, now=0.2)
        self.assertNotEqual(research_tui.companion_popup(st, now=0.2), weak_pop)  # interrupted/changed

    def test_idle_nudge_fires_once_and_rearms_after_interaction(self):
        st = self._state_with_two_studies()
        st.popup_selection_id = research_tui._selected_study_id(st)
        t0 = 1000.0   # a realistic monotonic base (0.0 reads as "never interacted")
        research_tui.note_interaction(st, now=t0)
        research_tui.update_companion_popups(st, now=t0 + research_tui.IDLE_SECS + 1)
        nudge = research_tui.companion_popup(st, now=t0 + research_tui.IDLE_SECS + 1)
        self.assertIsNotNone(nudge)
        self.assertTrue(st.idle_popped)
        born = st.popup_born
        research_tui.update_companion_popups(st, now=t0 + research_tui.IDLE_SECS + 5)
        self.assertEqual(st.popup_born, born)                                   # does not re-fire
        research_tui.note_interaction(st, now=t0 + 100.0)
        self.assertFalse(st.idle_popped)                                        # an interaction re-arms

    def test_long_running_job_nudges_repeatedly_over_time(self):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[], theme='bloomberg')
        st.busy = True
        # First nudge fires once past SEARCH_LONG_SECS …
        st.spinner_elapsed = research_tui.SEARCH_LONG_SECS + 1
        research_tui.update_companion_popups(st, now=100.0)
        first = research_tui.companion_popup(st, now=100.0)
        self.assertIsNotNone(first)
        born1 = st.popup_born
        # … and does NOT re-fire while elapsed hasn't advanced past the next gap …
        research_tui.update_companion_popups(st, now=101.0)
        self.assertEqual(st.popup_born, born1)
        # … but fires AGAIN once the job drags BUSY_NUDGE_EVERY seconds further.
        st.spinner_elapsed = research_tui.SEARCH_LONG_SECS + research_tui.BUSY_NUDGE_EVERY + 1
        research_tui.update_companion_popups(st, now=130.0)
        self.assertNotEqual(st.popup_born, born1)         # a second wait-nudge fired
        self.assertEqual(st.busy_nudge_i, 2)
        # The counter re-arms when the job ends.
        st.busy = False
        research_tui.update_companion_popups(st, now=131.0)
        self.assertEqual(st.busy_nudge_i, 0)

    def test_meta_run_uses_meta_flavoured_wait_banter(self):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[], theme='bloomberg')
        st.busy = True
        st.spinner_elapsed = research_tui.SEARCH_LONG_SECS + 1
        st.task = research_tui.BackgroundTask(kind='meta', verb='Meta-analyzing', query='q',
                                              worker=lambda: None, apply=lambda l, r: 'done')
        research_tui.update_companion_popups(st, now=100.0)
        pop = research_tui.companion_popup(st, now=100.0)
        self.assertTrue(any(w in (pop or '').lower()
                            for w in ('effect size', 'heterogeneity', 'confidence', 'synthesi', 'quality')))

    def test_finished_search_raises_a_popup(self):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[])
        st.busy = True
        task = research_tui.BackgroundTask(
            kind='search', verb='Investigating', query='x', worker=lambda: None,
            apply=lambda live, res: 'found 3 studies · best hot (84)')
        task.done = True
        task.started = 1.0
        st.task = task
        research_tui.poll_background_task(st, now=2.0)
        self.assertEqual(research_tui.companion_popup(st, now=2.0), 'found 3 studies · best hot (84)')

    def test_verdict_popup_survives_the_next_frame_with_results_selected(self):
        # The live-loop bug the unit test above missed: with real results selected,
        # the NEXT frame's update_companion_popups must not clobber the verdict.
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[], theme='bloomberg')
        st.busy = True
        st.active_tab = 'current'
        st.focus = 'articles'

        def apply(live, res):
            live.current_articles = [{'id': 4242, 'title': 'Top hit', 'final_score': 84, 'label': 'HOT'}]
            live.selected_article = 0
            return 'found 3 studies · best hot (84)'

        task = research_tui.BackgroundTask(
            kind='search', verb='Investigating', query='x', worker=lambda: None, apply=apply)
        task.done = True
        task.started = 1.0
        st.task = task
        research_tui.poll_background_task(st, now=2.0)
        # next frame: the selection trigger must NOT replace the verdict
        research_tui.update_companion_popups(st, now=2.1)
        self.assertEqual(research_tui.companion_popup(st, now=2.1), 'found 3 studies · best hot (84)')
        # but navigating to a DIFFERENT study still re-pops
        st.current_articles.append({'id': 99, 'title': 'Other', 'final_score': 20, 'label': 'RISK'})
        st.selected_article = 1
        research_tui.note_interaction(st, now=3.0)
        research_tui.update_companion_popups(st, now=3.1)
        self.assertNotEqual(research_tui.companion_popup(st, now=3.1), 'found 3 studies · best hot (84)')


class BackgroundSearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'research.db'
        ResearchStore(self.db_path).close()  # materialize the schema

    def tearDown(self):
        self.tmp.cleanup()

    def test_make_search_task_runs_on_scratch_state_and_applies_to_live(self):
        live = research_tui.load_state(self.db_path, limit=20)
        captured = {}

        def fake_sast(scratch, query, **kw):
            # The worker must operate on an isolated scratch state, never the live one.
            captured['used_live'] = scratch is live
            scratch.current_articles = [{'id': 999, 'title': 'Scratch result', 'final_score': 10}]
            scratch.saved_articles = list(scratch.current_articles)
            scratch.active_tab = 'current'
            scratch.focus = 'articles'
            scratch.last_query = query
            return '1 article stored'

        orig = research_tui.search_and_store_from_tui
        research_tui.search_and_store_from_tui = fake_sast
        try:
            task = research_tui.make_search_task(
                live, 'minoxidil', verb='Investigating', max_results=1, sources=['pubmed'])
            research_tui.start_background_task(live, task)
            self.assertTrue(live.busy)          # marked busy synchronously at launch
            task.thread.join(2.0)
            applied = research_tui.poll_background_task(live)
        finally:
            research_tui.search_and_store_from_tui = orig

        self.assertTrue(applied)
        self.assertFalse(captured['used_live'])  # invariant: worker never touched live state
        self.assertEqual([a['title'] for a in live.current_articles], ['Scratch result'])
        self.assertEqual(live.active_tab, 'current')
        self.assertFalse(live.busy)
        toast = research_tui.companion_notification(live, now=live.notif_born + 0.1)
        self.assertIn('found 1 study', toast)

    def test_deep_search_injects_and_persists_ai_summary(self):
        deep_text = ("CONTEXT & OBJECTIVE: evaluate oral minoxidil.\n"
                     "CRITICAL APPRAISAL: moderate risk of bias, small sample.\n"
                     "EVIDENCE LEVEL & VERDICT: Moderate certainty — useful but confirmatory.")

        def deep_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            self.assertTrue(deep)  # the depth slider must reach the runner as deep=True
            return [research_tui.ResearchArticle(
                title='Deep AI study', source='PubMed', doi='10.1/deepai',
                abstract='human randomized controlled trial n=200 safety outcome',
                deep_summary=deep_text, summary_method='full_text_pymupdf')]

        state = research_tui.load_state(self.db_path, limit=20)
        msg = research_tui.search_and_store_from_tui(
            state, 'oral minoxidil', max_results=1, sources=['pubmed'],
            deep=True, runner=deep_runner)
        self.assertIn('1 article', msg)
        # carried on the in-memory current row …
        self.assertEqual(state.current_articles[0].get('deep_summary'), deep_text)
        # … surfaced in the detail view as a dedicated AI section …
        detail = research_tui.render_detail_text(state, width=100)
        self.assertIn('AI DEEP SUMMARY', detail)
        self.assertIn('CRITICAL APPRAISAL', detail)
        self.assertIn('full_text_pymupdf', detail)
        # … the EVALUATION tab (labelled CRITICAL APPRAISAL) leads with the AI verdict …
        state.active_tab = 'evaluation'
        evaluation = research_tui.render_evaluation_text(state, width=100)
        self.assertIn('AI CRITICAL APPRAISAL', evaluation)
        self.assertIn('Moderate certainty', evaluation)
        self.assertIn('HEURISTIC SIGNALS', evaluation)
        # … and persisted to the summaries table so it survives a reload.
        store = ResearchStore(self.db_path)
        try:
            aid = int(state.current_articles[0]['id'])
            rows = store.conn.execute(
                'SELECT raw_text FROM summaries WHERE article_id=?', (aid,)).fetchall()
        finally:
            store.close()
        self.assertTrue(any('CRITICAL APPRAISAL' in (r['raw_text'] or '') for r in rows))

    def test_deep_summary_survives_reload_and_resurfaces_in_detail(self):
        deep_text = ("KEY FINDINGS: oral minoxidil increased hair count.\n"
                     "CRITICAL APPRAISAL: low risk of bias, adequately powered.\n"
                     "EVIDENCE LEVEL & VERDICT: High certainty — practice-relevant.")

        def deep_runner(query, max_results, lang, sources, deep=False, allow_scihub=False):
            return [research_tui.ResearchArticle(
                title='Durable deep study', source='PubMed', doi='10.1/durable',
                abstract='human randomized controlled trial n=400 outcome',
                deep_summary=deep_text, summary_method='full_text_pymupdf')]

        state = research_tui.load_state(self.db_path, limit=20)
        research_tui.search_and_store_from_tui(
            state, 'durable', max_results=1, sources=['pubmed'],
            deep=True, runner=deep_runner)

        # Simulate a reload / fresh session: saved rows now come from list_articles,
        # NOT the in-memory injection — they must still carry the AI synthesis.
        reloaded = research_tui.load_state(self.db_path, limit=20)
        match = next(a for a in reloaded.saved_articles if a.get('title') == 'Durable deep study')
        self.assertEqual(match.get('deep_summary'), deep_text)  # backfilled from raw_json
        reloaded.active_tab = 'saved'
        reloaded.selected_article = reloaded.saved_articles.index(match)
        detail = research_tui.render_detail_text(reloaded, width=100)
        self.assertIn('AI DEEP SUMMARY', detail)
        self.assertIn('CRITICAL APPRAISAL', detail)


class MetaAnalysisStorageTests(unittest.TestCase):
    """The meta_analyses table stores each compiled document once, with CRUD."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'm.db'

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_list_get_delete_and_ordering(self):
        store = ResearchStore(self.db_path)
        try:
            mid = store.add_meta_analysis(
                'topic X', '# Doc\n\nbody', lang='en', n_studies=3, depth='deep',
                md_path='/tmp/x.md', created_at='2026-06-30T10:00:00Z')
            self.assertGreater(mid, 0)
            rows = store.list_meta_analyses()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['query'], 'topic X')
            self.assertEqual(rows[0]['n_studies'], 3)
            self.assertEqual(rows[0]['document_md'], '# Doc\n\nbody')
            got = store.get_meta_analysis(mid)
            self.assertEqual(got['md_path'], '/tmp/x.md')
            self.assertEqual(got['lang'], 'en')
            # Most-recent-first ordering.
            store.add_meta_analysis('topic Y', 'y-body', created_at='2026-06-30T11:00:00Z')
            self.assertEqual([r['query'] for r in store.list_meta_analyses()], ['topic Y', 'topic X'])
            # Delete is idempotent-ish: True the first time, False after.
            self.assertTrue(store.delete_meta_analysis(mid))
            self.assertFalse(store.delete_meta_analysis(mid))
            self.assertIsNone(store.get_meta_analysis(mid))
            self.assertEqual(len(store.list_meta_analyses()), 1)
        finally:
            store.close()

    def test_table_autocreates_on_preexisting_db(self):
        # A DB created before this feature must gain the table on next open (the
        # schema runs CREATE TABLE IF NOT EXISTS every connect — no migration).
        ResearchStore(self.db_path).close()
        store = ResearchStore(self.db_path)
        try:
            self.assertEqual(store.list_meta_analyses(), [])
            store.add_meta_analysis('q', 'doc')
            self.assertEqual(len(store.list_meta_analyses()), 1)
        finally:
            store.close()


class MetaMarkdownWriterTests(unittest.TestCase):
    def test_writes_to_explicit_base(self):
        import file_writer
        with tempfile.TemporaryDirectory() as d:
            path = file_writer.write_meta_markdown(
                'Oxandrolone risks', '# Méta-analyse\n\nbody',
                created_at='2026-06-30T10:00:00Z', base=d)
            p = Path(path)
            self.assertTrue(p.exists())
            self.assertEqual(p.parent, Path(d))
            self.assertIn('2026-06-30', p.name)
            self.assertIn('Oxandrolone', p.name)
            self.assertEqual(p.read_text(encoding='utf-8'), '# Méta-analyse\n\nbody')

    def test_env_override_is_honoured(self):
        import file_writer
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict('os.environ', {'DR_NEWPAPER_META_DIR': d}):
                path = file_writer.write_meta_markdown('q', 'x', created_at='2026-06-30T10:00:00Z')
            self.assertEqual(Path(path).parent, Path(d))

    def test_same_query_twice_does_not_collide(self):
        import file_writer
        with tempfile.TemporaryDirectory() as d:
            a = file_writer.write_meta_markdown('same', 'doc A', created_at='2026-06-30T10:00:00Z', base=d)
            b = file_writer.write_meta_markdown('same', 'doc B', created_at='2026-06-30T10:00:00Z', base=d)
            self.assertNotEqual(a, b)
            self.assertEqual(Path(a).read_text(), 'doc A')
            self.assertEqual(Path(b).read_text(), 'doc B')


class MetaTabUITests(unittest.TestCase):
    """The Meta-Analyses tab: list, open, scroll, navigate, and background apply."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'm.db'
        self._prev_meta_dir = os.environ.get('DR_NEWPAPER_META_DIR')
        os.environ['DR_NEWPAPER_META_DIR'] = str(Path(self.tmp.name) / 'meta')
        store = ResearchStore(self.db_path)
        store.add_meta_analysis(
            'alpha topic', '# Méta-analyse : alpha\n\n' + '\n'.join(f'line {i}' for i in range(60)),
            n_studies=4, md_path='/tmp/a.md', created_at='2026-06-29T09:00:00Z')
        store.add_meta_analysis(
            'beta topic', '# Méta-analyse : beta\n\nshort body', n_studies=2,
            md_path='/tmp/b.md', created_at='2026-06-30T09:00:00Z')
        store.close()

    def tearDown(self):
        if self._prev_meta_dir is None:
            os.environ.pop('DR_NEWPAPER_META_DIR', None)
        else:
            os.environ['DR_NEWPAPER_META_DIR'] = self._prev_meta_dir
        self.tmp.cleanup()

    def test_load_state_loads_meta_list_most_recent_first(self):
        state = research_tui.load_state(self.db_path)
        self.assertEqual([m['query'] for m in state.meta_analyses], ['beta topic', 'alpha topic'])

    def test_advance_tab_cycles_all_five_tabs_and_back(self):
        state = research_tui.load_state(self.db_path)
        observed = []
        for _ in range(7):
            observed.append((state.active_tab, state.focus))
            research_tui.advance_tab(state)
        self.assertEqual(observed, [
            ('current', 'articles'), ('saved', 'articles'), ('evaluation', 'articles'),
            ('watchlist', 'watchlists'), ('watchlist', 'articles'),
            ('meta', 'articles'), ('current', 'articles'),
        ])
        # No tab is skipped.
        self.assertEqual({t for t, _ in observed},
                         {'current', 'saved', 'evaluation', 'watchlist', 'meta'})

    def test_entering_meta_tab_opens_selected_document(self):
        state = research_tui.load_state(self.db_path)
        state.active_tab, state.focus = 'watchlist', 'articles'
        research_tui.advance_tab(state)  # → meta, opens the highlighted run
        self.assertEqual(state.active_tab, 'meta')
        self.assertEqual(state.selected_meta, 0)
        self.assertEqual(state.meta_query, 'beta topic')
        self.assertIn('beta', state.meta_document)
        self.assertEqual(state.meta_scroll, 0)

    def test_open_selected_meta_switches_document_and_resets_scroll(self):
        state = research_tui.load_state(self.db_path)
        research_tui.open_selected_meta(state)
        state.meta_scroll = 5
        # Emulate the `]` key: move to the next (older) run, reopen.
        state.selected_meta = research_tui.clamp_index(state.selected_meta + 1, len(state.meta_analyses))
        research_tui.open_selected_meta(state)
        self.assertEqual(state.meta_query, 'alpha topic')
        self.assertEqual(state.meta_scroll, 0)

    def test_render_meta_document_has_header_and_body(self):
        state = research_tui.load_state(self.db_path)
        research_tui.open_selected_meta(state)
        doc = research_tui.render_meta_document_text(state, width=80)
        self.assertIn('META-ANALYSIS', doc)          # metadata header line
        self.assertIn('file: /tmp/b.md', doc)         # the .md location is surfaced
        self.assertIn('Méta-analyse : beta', doc)     # the document body

    def test_meta_scroll_clamps_to_document_bounds(self):
        # Past-the-end scroll is pulled back so the last line stays visible; the
        # _draw loop applies exactly this clamp each frame.
        self.assertEqual(research_tui.clamp_scroll(0, 0), 0)
        self.assertEqual(research_tui.clamp_scroll(5, 0), 0)
        self.assertEqual(research_tui.clamp_scroll(-3, 10), 0)
        self.assertEqual(research_tui.clamp_scroll(9999, 40), 39)
        self.assertEqual(research_tui.clamp_scroll(12, 40), 12)

    def test_render_meta_document_empty_state(self):
        empty_db = Path(self.tmp.name) / 'empty.db'
        ResearchStore(empty_db).close()
        state = research_tui.load_state(empty_db)
        self.assertEqual(state.meta_analyses, [])
        self.assertIn('No meta-analysis yet', research_tui.render_meta_document_text(state, width=80))

    def test_background_meta_task_propagates_document_to_live_state(self):
        # The worker runs on a scratch state; apply must carry both the list and
        # the open document onto the live state (advisor landmine #1).
        def fake_run(scratch, query, max_articles=8, lang='fr', meta_sources=None, analysis_depth='medium', progress_cb=None, result_cb=None):
            scratch.meta_analyses = [{'id': 1, 'query': query, 'n_studies': 2, 'lang': 'fr',
                                      'document_md': '# Doc\n\nbody', 'md_path': '/tmp/x.md',
                                      'created_at': '2026-06-30T10:00:00Z'}]
            scratch.selected_meta = 0
            scratch.meta_document = '# Doc\n\nbody'
            scratch.meta_query = query
            scratch.meta_scroll = 0
            scratch.active_tab = 'meta'
            scratch.focus = 'articles'
            scratch.current_articles = []
            return 'ok'

        live = research_tui.load_state(self.db_path)
        with mock.patch.object(research_tui, 'run_meta_analysis_from_tui', fake_run):
            task = research_tui.make_search_task(live, 'gamma', verb='Meta',
                                                 max_results=2, kind='meta', meta=True)
            scratch = task.worker()
            toast = task.apply(live, scratch)
        self.assertEqual(live.active_tab, 'meta')
        self.assertEqual(live.meta_document, '# Doc\n\nbody')
        self.assertEqual(live.meta_query, 'gamma')
        self.assertEqual(len(live.meta_analyses), 1)
        self.assertIn('analyzed', toast)

    def test_normal_search_keeps_an_open_meta_document(self):
        # A non-meta search must NOT wipe a document the user had open.
        live = research_tui.load_state(self.db_path)
        research_tui.open_selected_meta(live)
        live.meta_document = 'KEEP ME'
        live.meta_query = 'kept'
        live.meta_scroll = 7

        def fake_search(scratch, query, **kw):
            scratch.current_articles = []
            scratch.active_tab = 'current'
            return 'ok'

        with mock.patch.object(research_tui, 'search_and_store_from_tui', fake_search):
            task = research_tui.make_search_task(live, 'delta', verb='Search',
                                                 max_results=2, meta=False)
            scratch = task.worker()
            task.apply(live, scratch)
        self.assertEqual(live.meta_document, 'KEEP ME')
        self.assertEqual(live.meta_query, 'kept')
        self.assertEqual(live.meta_scroll, 7)

    def test_meta_tab_selects_no_article_so_global_keys_noop(self):
        # On the Meta tab no article row is drawn; w/a/p must not act on the
        # invisible current_articles row (selected_article returns None).
        state = research_tui.load_state(self.db_path)
        state.active_tab, state.focus = 'meta', 'articles'
        state.current_articles = [{'id': 999, 'title': 'invisible included study'}]
        self.assertIsNone(research_tui.selected_article(state))
        self.assertIn('No article selected', research_tui.evaluate_selected_article(state))

    def test_refresh_state_reloads_meta_list_newest_first(self):
        state = research_tui.load_state(self.db_path)
        n0 = len(state.meta_analyses)
        store = ResearchStore(self.db_path)
        try:
            store.add_meta_analysis('added later', '# later', created_at='2026-07-01T00:00:00Z')
        finally:
            store.close()
        research_tui.refresh_state(state)
        self.assertEqual(len(state.meta_analyses), n0 + 1)
        self.assertEqual(state.meta_analyses[0]['query'], 'added later')

    def test_landing_selects_inserted_run_not_list_position(self):
        # A pre-existing run dated in the FUTURE would sort to index 0; landing
        # must still open the run we just inserted (selected by id, not position).
        store = ResearchStore(self.db_path)
        try:
            store.add_meta_analysis('future run', '# future', created_at='2099-01-01T00:00:00Z')
        finally:
            store.close()

        def fake_meta(query, max_articles=8, deep=False, lang='fr'):
            return {'query': query, 'summary': '# New synthesis\n\nbody', 'n_studies': 1, 'lang': 'fr',
                    'articles': [{'title': 'S', 'doi': '10.1/s', 'date': '2026-01-01',
                                  'abstract': 'randomized controlled trial n=10 outcome'}]}

        state = research_tui.load_state(self.db_path)
        research_tui.run_meta_analysis_from_tui(state, 'brand new', runner=fake_meta)
        self.assertEqual(state.active_tab, 'meta')
        self.assertEqual(state.meta_query, 'brand new')          # not 'future run'
        self.assertIn('New synthesis', state.meta_document)
        self.assertEqual(state.meta_analyses[state.selected_meta]['query'], 'brand new')

    def test_render_meta_document_lines_never_exceed_width(self):
        # A long absolute .md path in the header must wrap, not overrun the box.
        long_path = '/home/user/' + 'verylongdir/' * 15 + 'file.md'
        store = ResearchStore(self.db_path)
        try:
            store.add_meta_analysis('pathy', '# Doc\n\nbody', n_studies=1,
                                    md_path=long_path, created_at='2026-07-02T00:00:00Z')
        finally:
            store.close()
        state = research_tui.load_state(self.db_path)
        state.selected_meta = 0
        research_tui.open_selected_meta(state)
        width = 50
        for line in research_tui.render_meta_document_text(state, width=width).splitlines():
            self.assertLessEqual(len(line), max(30, width - 2), f'overflowing line: {line!r}')

    def test_empty_summary_does_not_persist_or_land_on_meta(self):
        def empty_runner(query, max_articles=8, deep=False, lang='fr'):
            return {'query': query, 'summary': '   ', 'n_studies': 0, 'articles': [], 'lang': 'fr'}

        state = research_tui.load_state(self.db_path)
        before = len(state.meta_analyses)
        msg = research_tui.run_meta_analysis_from_tui(state, 'voidquery', runner=empty_runner)
        self.assertIn('no readable document', msg)
        self.assertNotEqual(state.active_tab, 'meta')
        store = ResearchStore(self.db_path)
        try:
            self.assertEqual(len(store.list_meta_analyses()), before)  # nothing persisted
        finally:
            store.close()


class StudyReadingModeTests(unittest.TestCase):
    """Enter on a study opens its full detail for scrolling (↑/↓, PgUp/PgDn)."""

    def _state_with_study(self):
        st = research_tui.TuiState(db_path=Path('.'), articles=[], watchlists=[])
        st.current_articles = [{
            'id': 1, 'title': 'Long study', 'label': 'HOT', 'final_score': 80,
            'deep_summary': '\n'.join(f'summary line {i}' for i in range(40)),
            'abstract': '\n'.join(f'abstract para {i}' for i in range(40)),
        }]
        st.active_tab, st.focus, st.selected_article = 'current', 'articles', 0
        return st

    def test_enter_reading_on_a_selected_study(self):
        st = self._state_with_study()
        self.assertTrue(research_tui.enter_reading(st))
        self.assertTrue(st.reading)
        self.assertEqual(st.detail_scroll, 0)

    def test_enter_reading_is_noop_without_selection_or_on_meta_or_themes(self):
        st = self._state_with_study()
        st.current_articles = []                       # nothing selected
        self.assertFalse(research_tui.enter_reading(st))
        self.assertFalse(st.reading)
        st_meta = self._state_with_study(); st_meta.active_tab = 'meta'
        self.assertFalse(research_tui.enter_reading(st_meta))  # meta has its own reader
        st_themes = self._state_with_study(); st_themes.focus = 'watchlists'
        self.assertFalse(research_tui.enter_reading(st_themes))

    def test_reading_keys_scroll_then_exit(self):
        st = self._state_with_study()
        research_tui.enter_reading(st)
        research_tui.reading_handle_key(st, curses.KEY_DOWN)
        self.assertEqual(st.detail_scroll, 1)
        research_tui.reading_handle_key(st, curses.KEY_NPAGE)
        self.assertEqual(st.detail_scroll, 1 + research_tui.PAGE_LINES)
        research_tui.reading_handle_key(st, curses.KEY_UP)
        self.assertEqual(st.detail_scroll, research_tui.PAGE_LINES)
        research_tui.reading_handle_key(st, curses.KEY_PPAGE)
        self.assertEqual(st.detail_scroll, 0)
        research_tui.reading_handle_key(st, curses.KEY_UP)   # never below zero
        self.assertEqual(st.detail_scroll, 0)
        research_tui.reading_handle_key(st, 27)              # Esc exits
        self.assertFalse(st.reading)
        self.assertEqual(st.detail_scroll, 0)

    def test_space_pages_down_and_enter_exits(self):
        st = self._state_with_study()
        research_tui.enter_reading(st)
        research_tui.reading_handle_key(st, ord(' '))
        self.assertEqual(st.detail_scroll, research_tui.PAGE_LINES)
        research_tui.reading_handle_key(st, 10)              # Enter exits
        self.assertFalse(st.reading)

    def test_full_summary_and_abstract_are_available_to_scroll(self):
        # render_detail_text exposes the WHOLE study (what reading mode scrolls),
        # including the tail that the clipped pane would otherwise hide.
        st = self._state_with_study()
        text = research_tui.render_detail_text(st, width=80)
        self.assertIn('AI DEEP SUMMARY', text)
        self.assertIn('summary line 39', text)
        self.assertIn('abstract para 39', text)

    def test_exit_reading_resets_scroll(self):
        st = self._state_with_study()
        research_tui.enter_reading(st)
        st.detail_scroll = 12
        research_tui.exit_reading(st)
        self.assertFalse(st.reading)
        self.assertEqual(st.detail_scroll, 0)

    def test_detail_title_shows_reading_marker_on_every_article_tab(self):
        st = self._state_with_study()
        # No marker before reading.
        for tab in ('current', 'saved', 'evaluation', 'watchlist'):
            st.active_tab, st.reading = tab, False
            self.assertNotIn('READING', research_tui._detail_pane_title(st))
        # Marker on every article tab once reading (watchlist studies included).
        for tab in ('current', 'saved', 'evaluation', 'watchlist'):
            st.active_tab, st.reading = tab, True
            self.assertIn('READING', research_tui._detail_pane_title(st),
                          f'missing READING marker on {tab}')


class ScihubEnabledTests(unittest.TestCase):
    """config.scihub_enabled() is the single opt-in resolver."""

    def test_explicit_value_always_wins(self):
        with mock.patch.dict('os.environ', {'DR_NEWPAPER_ALLOW_SCIHUB': '0'}):
            self.assertTrue(config.scihub_enabled(True))   # explicit on beats env-off
        with mock.patch.dict('os.environ', {'DR_NEWPAPER_ALLOW_SCIHUB': '1'}):
            self.assertFalse(config.scihub_enabled(False))  # explicit off beats env-on

    def test_default_is_off_when_unset(self):
        with mock.patch.dict('os.environ', {}, clear=False):
            import os as _os
            _os.environ.pop('DR_NEWPAPER_ALLOW_SCIHUB', None)
            self.assertFalse(config.scihub_enabled())
            self.assertFalse(config.scihub_enabled(None))

    def test_operator_must_opt_in(self):
        # …including whitespace-padded values, which an .env can easily carry.
        for on in ('1', 'true', 'yes', 'on', 'ON', ' 1', '1 ', '  on ', 'true\n'):
            with mock.patch.dict('os.environ', {'DR_NEWPAPER_ALLOW_SCIHUB': on}):
                self.assertTrue(config.scihub_enabled(), f'{on!r} should opt in')
        # An allow-list, so anything unrecognised fails closed rather than on.
        for off in ('0', 'false', 'no', 'off', '', 'maybe', '2'):
            with mock.patch.dict('os.environ', {'DR_NEWPAPER_ALLOW_SCIHUB': off}):
                self.assertFalse(config.scihub_enabled(), f'{off!r} should stay off')


class MetaAnalysisSourcesTests(unittest.TestCase):
    """Direct tests for perform_meta_analysis: source routing and PDF-cap lift."""

    def _make_recording_map(self, calls):
        """Return a patched _SEARCHER_MAP where every invocation is recorded."""
        def _make(name):
            def fn(q, max_results=8):
                calls.append(name)
                return []
            return fn
        return {k: _make(k) for k in meta_analysis._SEARCHER_MAP}

    def test_sources_filter_restricts_which_searchers_are_called(self):
        calls = []
        with mock.patch.dict(meta_analysis._SEARCHER_MAP, self._make_recording_map(calls)):
            meta_analysis.perform_meta_analysis("q", sources=["pubmed", "openalex"])
        self.assertIn("pubmed", calls)
        self.assertIn("openalex", calls)
        self.assertNotIn("crossref", calls)
        self.assertNotIn("europe_pmc", calls)

    def test_all_sources_called_when_none_specified(self):
        calls = []
        with mock.patch.dict(meta_analysis._SEARCHER_MAP, self._make_recording_map(calls)):
            meta_analysis.perform_meta_analysis("q")
        for name in list(meta_analysis._SEARCHER_MAP.keys()):
            self.assertIn(name, calls, f"{name} should have been called")

    def test_full_text_limit_equals_article_count_not_capped_at_five(self):
        # The old cap was min(5, len(articles)). Verify it is now len(articles).
        fake_articles = [
            {"title": f"Study {i}", "abstract": "rct placebo n=100", "doi": f"10.1/s{i}"}
            for i in range(8)
        ]

        def fake_searcher(q, max_results=8):
            return fake_articles

        captured_limit = []

        def fake_enrich(articles, limit, allow_scihub=None, progress=None):
            captured_limit.append(limit)

        patched_map = {k: fake_searcher for k in meta_analysis._SEARCHER_MAP}
        with (mock.patch.dict(meta_analysis._SEARCHER_MAP, patched_map),
              mock.patch.object(meta_analysis, "_enrich_full_text", fake_enrich),
              mock.patch.object(meta_analysis.minimax_client, "chat", return_value="synthesis")):
            meta_analysis.perform_meta_analysis("q", max_articles=8, deep=True)

        self.assertEqual(len(captured_limit), 1, "_enrich_full_text should be called once")
        self.assertEqual(captured_limit[0], 8, "limit must equal len(articles), not min(5, 8)=5")


if __name__ == '__main__':
    unittest.main()
