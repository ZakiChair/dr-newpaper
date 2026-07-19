import unittest

from research_terminal import (
    ResearchArticle,
    build_scorecard,
    format_sources_bar,
    normalize_articles,
)


class ResearchTerminalFormattingTests(unittest.TestCase):
    def test_normalize_articles_preserves_core_fields(self):
        raw = [{
            "title": "A clinical trial",
            "source": "pubmed",
            "date": "2026-01-02",
            "authors": ["A", "B", "C"],
            "doi": "10.123/test",
            "journal": "NEJM",
            "summary": "Useful summary",
            "url": "https://example.test",
        }]

        articles = normalize_articles(raw)

        self.assertEqual(len(articles), 1)
        self.assertIsInstance(articles[0], ResearchArticle)
        self.assertEqual(articles[0].title, "A clinical trial")
        self.assertEqual(articles[0].source, "pubmed")
        self.assertEqual(articles[0].doi, "10.123/test")
        self.assertEqual(articles[0].authors, ["A", "B", "C"])

    def test_scorecard_counts_sources_dois_and_pdf_availability(self):
        articles = normalize_articles([
            {"title": "A", "source": "pubmed", "doi": "10/a", "oa_pdf_url": "https://pdf.test/a.pdf"},
            {"title": "B", "source": "openalex", "doi": "10/b"},
            {"title": "C", "source": "pubmed"},
        ])

        card = build_scorecard(articles)

        self.assertEqual(card["articles"], 3)
        self.assertEqual(card["sources"], {"pubmed": 2, "openalex": 1})
        self.assertEqual(card["with_doi"], 2)
        self.assertEqual(card["with_pdf"], 1)

    def test_sources_bar_marks_selected_sources(self):
        text = format_sources_bar(["pubmed", "openalex"], ["pubmed", "crossref", "openalex"])

        self.assertIn("[x] pubmed", text)
        self.assertIn("[ ] crossref", text)
        self.assertIn("[x] openalex", text)

    def test_best_summary_hides_minimax_errors_when_abstract_exists(self):
        article = ResearchArticle(
            title="A",
            summary="[MiniMax error: The read operation timed out]",
            abstract="This abstract is still useful.",
        )

        self.assertEqual(article.best_summary, "This abstract is still useful.")

    def test_best_summary_uses_clean_unavailable_message_when_only_error_exists(self):
        article = ResearchArticle(
            title="A",
            summary="[Erreur MiniMax: timeout]",
        )

        self.assertEqual(article.best_summary, "Résumé automatique indisponible pour cet article.")


if __name__ == "__main__":
    unittest.main()
