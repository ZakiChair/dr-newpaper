import os
import unittest
from unittest.mock import patch

import meta_analysis
import minimax_client


class MiniMaxM3ConfigTests(unittest.TestCase):
    def test_summarizer_defaults_to_m3(self):
        self.assertEqual(minimax_client.DEFAULT_MODEL, "MiniMax-M3")

    def test_meta_analysis_defaults_to_m3(self):
        self.assertEqual(meta_analysis.DEFAULT_MODEL, "MiniMax-M3")

    def test_get_key_uses_existing_environment_key_without_replacing_it(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-existing-test-key"}, clear=False):
            self.assertEqual(minimax_client._get_key(), "sk-existing-test-key")

    def test_payload_for_m3_keeps_bot_setting_and_uses_configured_model(self):
        payload = minimax_client._build_payload("prompt", model="MiniMax-M3", temperature=0.2, max_tokens=777)

        self.assertEqual(payload["model"], "MiniMax-M3")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "prompt"}])
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 777)
        self.assertIn("bot_setting", payload)
        self.assertEqual(payload["bot_setting"][0]["bot_name"], "Dr_NewPaper")

    def test_summarize_article_prompt_switches_with_lang(self):
        # summarize_article accepted a `lang` param but never read it — every
        # short (non-deep) summary was silently generated in French regardless
        # of the caller's requested language. Guard the fix: the prompt body
        # sent to MiniMax must actually change with `lang`.
        article = {"title": "T", "authors": ["A"], "source": "pubmed",
                   "date": "2024", "abstract": "some abstract text"}
        captured = {}

        def fake_post(payload, timeout=60):
            captured["prompt"] = payload["messages"][0]["content"]
            return {"content": "ok"}

        with patch.object(minimax_client, "_get_key", return_value="sk-test"), \
             patch.object(minimax_client, "_post", side_effect=fake_post):
            minimax_client.summarize_article(article, lang="fr")
            fr_prompt = captured["prompt"]
            minimax_client.summarize_article(article, lang="en")
            en_prompt = captured["prompt"]

        self.assertIn("Résume cet article", fr_prompt)
        self.assertIn("Summarize this academic article", en_prompt)
        self.assertNotEqual(fr_prompt, en_prompt)


if __name__ == "__main__":
    unittest.main()
