"""Tests for the security-relevant defaults: Sci-Hub opt-in, bot authorization, repo-local paths.

These lock down four properties the project must not silently regress:

1. Sci-Hub is *opt-in*, never reached by an operator who configured nothing.
2. The Telegram bot authorizes nobody unless an operator names a chat.
3. No personal identifier is baked into the (public) sources.
4. No file points at the pre-extraction monorepo path under /tmp.
"""
import os
import unittest
from pathlib import Path
from unittest import mock

import config
import file_writer

REPO = Path(__file__).resolve().parent
# Files that legitimately quote the forbidden strings: this test module itself.
_SELF = Path(__file__).name


# Generated research output now lands in <repo>/Dossier (it used to be written
# under /tmp). Those .md files are downloaded article text — arbitrary prose the
# scans below must not read, or a paper that happens to quote a long number
# would fail the leak test, and every scan would grow with the library.
_EXCLUDED_DIRS = {"__pycache__", ".git", "Dossier"}


def _scanned_files():
    """Every source/doc file in the repo, excluding this test and generated data."""
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".sh", ".md"}:
            continue
        if path.name == _SELF or _EXCLUDED_DIRS.intersection(path.parts):
            continue
        yield path


class SciHubOptInTests(unittest.TestCase):
    """Sci-Hub must require a deliberate operator opt-in, not an opt-out."""

    def test_off_when_no_environment_is_configured(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("DR_NEWPAPER_ALLOW_SCIHUB", None)
            self.assertFalse(config.scihub_enabled())
            self.assertFalse(config.scihub_enabled(None))

    def test_opt_in_values_enable_it(self):
        for on in ("1", "true", "TRUE", "yes", "on", " 1 "):
            with mock.patch.dict(os.environ, {"DR_NEWPAPER_ALLOW_SCIHUB": on}):
                self.assertTrue(config.scihub_enabled(), f"{on!r} should opt in")

    def test_anything_else_leaves_it_off(self):
        # Notably: an unrecognised value must fail *closed*, not fall through to on.
        for off in ("0", "false", "no", "off", "", "maybe", "2"):
            with mock.patch.dict(os.environ, {"DR_NEWPAPER_ALLOW_SCIHUB": off}):
                self.assertFalse(config.scihub_enabled(), f"{off!r} should stay off")

    def test_explicit_caller_argument_still_wins_both_ways(self):
        with mock.patch.dict(os.environ, {"DR_NEWPAPER_ALLOW_SCIHUB": "0"}):
            self.assertTrue(config.scihub_enabled(True))
        with mock.patch.dict(os.environ, {"DR_NEWPAPER_ALLOW_SCIHUB": "1"}):
            self.assertFalse(config.scihub_enabled(False))


class BotAuthorizationTests(unittest.TestCase):
    """The bot must authorize nobody by default, and only the declared chat otherwise."""

    def test_unset_chat_id_authorizes_nobody(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            self.assertFalse(config.is_authorized("6173476745"))
            self.assertFalse(config.is_authorized(0))
            self.assertFalse(config.is_authorized(None))

    def test_blank_chat_id_authorizes_nobody(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "   "}):
            self.assertFalse(config.is_authorized("123"))

    def test_only_the_declared_operator_passes(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123"}):
            self.assertTrue(config.is_authorized("123"))
            self.assertTrue(config.is_authorized(123))  # Telegram hands us an int
            self.assertFalse(config.is_authorized("1234"))
            self.assertFalse(config.is_authorized("-123"))
            self.assertFalse(config.is_authorized(None))


class NoLeakedIdentifierTests(unittest.TestCase):
    def test_no_personal_chat_id_is_hardcoded_anywhere(self):
        # The repo is public; a personal Telegram id must not ship as a fallback.
        leaked = [str(p.relative_to(REPO)) for p in _scanned_files()
                  if "6173476745" in p.read_text(encoding="utf-8", errors="ignore")]
        self.assertEqual(leaked, [], f"personal chat id still present in {leaked}")


class RepoLocalPathTests(unittest.TestCase):
    """Nothing may point at /tmp/hermy_repo — the pre-extraction monorepo location."""

    def test_no_file_references_the_old_monorepo_path(self):
        stale = [str(p.relative_to(REPO)) for p in _scanned_files()
                 if "hermy_repo" in p.read_text(encoding="utf-8", errors="ignore")]
        self.assertEqual(stale, [], f"stale /tmp path still referenced in {stale}")

    def test_dossier_base_lives_inside_the_repo(self):
        self.assertEqual(file_writer.RECHERCHE_BASE.parent, REPO)
        self.assertEqual(file_writer.RECHERCHE_BASE.name, "Dossier")


if __name__ == "__main__":
    unittest.main()
