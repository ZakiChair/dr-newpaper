import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import meta_analysis
import minimax_client

REPO = Path(__file__).resolve().parent


class ModelIsConfiguredInOnePlaceTests(unittest.TestCase):
    """One model name, and it is the one that reaches the API.

    There used to be two: config's, which read DR_NEWPAPER_MODEL, and
    minimax_client's hardcoded literal. Every summary and meta-analysis went out
    under the literal, so the documented setting could only change the name in
    send_digest's payload — nowhere it mattered.
    """

    def test_every_model_default_agrees_with_config(self):
        self.assertEqual(minimax_client.DEFAULT_MODEL, config.DEFAULT_MODEL)
        self.assertEqual(meta_analysis.DEFAULT_MODEL, config.DEFAULT_MODEL)

    def test_the_payload_carries_that_same_model(self):
        # The end of the chain: a default frozen into four signatures at import.
        self.assertEqual(minimax_client._build_payload("p")["model"],
                         config.DEFAULT_MODEL)

    def test_the_documented_default_survives_when_nothing_is_configured(self):
        # Asserted against the rule that applies, so an operator who set the
        # variable does not see the suite fail for using a documented setting.
        self.assertEqual(config.DEFAULT_MODEL,
                         os.getenv("DR_NEWPAPER_MODEL") or "MiniMax-M3")


class ModelFromEnvFileTests(unittest.TestCase):
    """A DR_NEWPAPER_MODEL written only in .env must reach the payload.

    In-process patching cannot show this: the four signatures in minimax_client
    freeze their default when the module is defined, so the whole chain has to
    be replayed in a fresh interpreter. config.py and minimax_client.py are
    copied out on their own — minimax_client imports nothing else of the repo.
    """

    def _model_seen_by(self, env_file_text, exported=None):
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        for name in ("config.py", "minimax_client.py"):
            shutil.copy(REPO / name, workdir / name)
        if env_file_text is not None:
            (workdir / ".env").write_text(env_file_text)

        env = {k: v for k, v in os.environ.items() if k != "DR_NEWPAPER_MODEL"}
        if exported is not None:
            env["DR_NEWPAPER_MODEL"] = exported
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import minimax_client; "
             "print(minimax_client._build_payload('p')['model'])" % str(workdir)],
            capture_output=True, text=True, timeout=60, env=env, cwd=str(workdir))
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip().splitlines()[-1]

    def test_a_value_present_only_in_the_env_file_reaches_the_payload(self):
        self.assertEqual(
            self._model_seen_by("DR_NEWPAPER_MODEL=MiniMax-DEPUIS-LE-FICHIER\n"),
            "MiniMax-DEPUIS-LE-FICHIER")

    def test_an_exported_value_wins_over_the_file(self):
        self.assertEqual(
            self._model_seen_by("DR_NEWPAPER_MODEL=MiniMax-DEPUIS-LE-FICHIER\n",
                                exported="MiniMax-EXPORTE"),
            "MiniMax-EXPORTE")

    def test_without_any_configuration_the_documented_default_applies(self):
        self.assertEqual(self._model_seen_by(None), "MiniMax-M3")


class MiniMaxM3ConfigTests(unittest.TestCase):

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
