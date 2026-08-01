"""Tests for the security-relevant defaults: Sci-Hub opt-in, bot authorization, repo-local paths.

These lock down four properties the project must not silently regress:

1. Sci-Hub is *opt-in*, never reached by an operator who configured nothing.
2. The Telegram bot authorizes nobody unless an operator names a chat.
3. No personal identifier is baked into the (public) sources.
4. No file points at the pre-extraction monorepo path under /tmp.
"""
import os
import re
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


class SharedChatAuthorizationTests(unittest.TestCase):
    """A chat allow-list authorizes a *place*; a group is a place others can enter.

    In a private chat the distinction is empty — Telegram gives the chat the
    user's own id — so nothing changes for the ordinary setup. A group or channel
    is the case that needs a second key: the same variable is also the digest
    destination, so pointing it at a group is a natural thing to do, and it would
    otherwise hand the bot to every current and future member.
    """

    def test_a_private_operator_chat_needs_no_user_list(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "123"}):
            os.environ.pop("TELEGRAM_ALLOWED_USERS", None)
            self.assertTrue(config.is_authorized(123, 123))
            self.assertTrue(config.is_authorized(123))  # historical one-argument call

    def test_a_group_operator_chat_authorizes_nobody_without_a_user_list(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "-1001234"}):
            os.environ.pop("TELEGRAM_ALLOWED_USERS", None)
            self.assertFalse(config.is_authorized(-1001234, 555))
            self.assertFalse(config.is_authorized(-1001234))

    def test_a_group_operator_chat_authorizes_only_listed_users(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "-1001234",
                                          "TELEGRAM_ALLOWED_USERS": "555, 666"}):
            self.assertTrue(config.is_authorized(-1001234, 555))
            self.assertTrue(config.is_authorized(-1001234, 666))
            self.assertFalse(config.is_authorized(-1001234, 777))
            self.assertFalse(config.is_authorized(-1001234, None))

    def test_the_user_list_never_widens_the_chat_check(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "-1001234",
                                          "TELEGRAM_ALLOWED_USERS": "555"}):
            self.assertFalse(config.is_authorized(-9999, 555))

    def test_an_unparsable_entry_is_dropped_rather_than_trusted(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": " 555 ,abc,,666 "}):
            self.assertEqual(config.allowed_user_ids(), {555, 666})

    def test_a_chat_id_pasted_into_the_user_list_is_rejected(self):
        # A negative id is a chat, not a person. Accepting it would make a list
        # that authorizes nobody look filled, and the bot would start.
        with mock.patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "-1001234, 0"}):
            self.assertEqual(config.allowed_user_ids(), set())

    def test_an_unset_list_is_empty_not_universal(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("TELEGRAM_ALLOWED_USERS", None)
            self.assertEqual(config.allowed_user_ids(), set())


class NoLeakedIdentifierTests(unittest.TestCase):
    def test_no_source_prints_a_slice_of_a_credential(self):
        # `BOT_TOKEN[:20]` and `BOT_TOKEN[-10:]` both went to stdout, and the
        # output of a restart script is exactly what gets pasted into an issue.
        # Matched on the *name* rather than the two known sites, so a third one
        # cannot appear quietly. The bot id — the part before the colon — is
        # public and stays printable; anything after it is the whole secret.
        pattern = re.compile(r"(?:TOKEN|API_KEY|SECRET|PASSWORD)\s*\[")
        offenders = sorted(
            f"{p.relative_to(REPO)}:{n}"
            for p in _scanned_files()
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore")
                                     .splitlines(), 1)
            if pattern.search(line)
        )
        self.assertEqual(offenders, [], f"credential sliced in {offenders}")

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

    def test_every_dossier_root_resolves_to_the_same_directory(self):
        # Articles, PDFs and exported dossiers must land in one library. When
        # some roots were anchored to this file and others to the working
        # directory, `cd ~ && research_terminal.py digest` filed its exports
        # under ~/Dossier while the articles they describe sat in the repo.
        import pdf_sender
        self.assertEqual(file_writer.RECHERCHE_BASE, config.DOSSIER_BASE)
        # Both are env-overridable and resolved at import. Asserting the
        # repo-anchored value outright would fail for an operator who moved
        # their library to an external volume — a supported thing to do — so
        # each is checked against whichever rule actually applies. Skipping
        # instead would leave the invariant unverified precisely for them.
        for constant, override in (
            (Path(pdf_sender.PDF_LIBRARY_DIR), os.getenv("DR_NEWPAPER_PDF_DIR")),
            (Path(file_writer.META_LIBRARY_DIR), os.getenv("DR_NEWPAPER_META_DIR")),
        ):
            with self.subTest(constant=str(constant)):
                if override:
                    self.assertEqual(constant, Path(override))
                else:
                    self.assertEqual(constant.parent, config.DOSSIER_BASE)

    def test_no_module_anchors_the_library_to_the_working_directory(self):
        # Bans the literal rather than comparing two constants: what must not
        # come back is a *fifth* root, wherever someone next needs one.
        offenders = sorted(
            str(p.relative_to(REPO)) for p in _scanned_files()
            if 'Path("Dossier")' in p.read_text(encoding="utf-8", errors="ignore")
            or "Path('Dossier')" in p.read_text(encoding="utf-8", errors="ignore")
        )
        self.assertEqual(offenders, [],
                         f"library root relative to the cwd in {offenders}")


if __name__ == "__main__":
    unittest.main()
