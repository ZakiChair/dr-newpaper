"""Tests for reading .env while config is being imported.

`config` reads the file in its own module body so that every value depending on
the environment is resolved from it, whatever order the modules load in. That
placement is what makes the setting work — and it is also what makes a bad file
dangerous: every module in the project imports config, so an exception raised
here takes down the whole project, the test suite with it. A file that only
supplies defaults must never be able to do that.

Each case runs in a fresh interpreter against a copy of config.py, so nothing
here depends on — or touches — the operator's own .env.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent


class ImportTimeEnvReadingTests(unittest.TestCase):

    def _import_config_beside(self, make_env):
        """Import a copy of config.py next to whatever `make_env` leaves there."""
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup, workdir)
        shutil.copy(REPO / "config.py", workdir / "config.py")
        make_env(workdir / ".env")
        env = {k: v for k, v in os.environ.items() if not k.startswith("DR_NEWPAPER_")}
        return subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import config; "
             "print(config.DEFAULT_MODEL)" % str(workdir)],
            capture_output=True, text=True, timeout=60, env=env, cwd=str(workdir))

    @staticmethod
    def _cleanup(workdir):
        env_file = workdir / ".env"
        if env_file.exists() and not env_file.is_dir():
            env_file.chmod(0o644)
        shutil.rmtree(workdir, ignore_errors=True)

    def _assert_survives(self, make_env, expect_warning):
        out = self._import_config_beside(make_env)
        self.assertEqual(out.returncode, 0,
                         f"importing config raised:\n{out.stderr}")
        self.assertEqual(out.stdout.strip().splitlines()[-1], "MiniMax-M3")
        if expect_warning:
            self.assertIn("Impossible de lire", out.stderr)

    def test_a_readable_file_is_applied(self):
        out = self._import_config_beside(
            lambda p: p.write_text("DR_NEWPAPER_MODEL=MiniMax-DU-FICHIER\n"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "MiniMax-DU-FICHIER")

    def test_an_unreadable_file_warns_instead_of_raising(self):
        def make(p):
            p.write_text("DR_NEWPAPER_MODEL=jamais-lu\n")
            p.chmod(0o000)
        self._assert_survives(make, expect_warning=True)

    def test_a_directory_named_env_warns_instead_of_raising(self):
        self._assert_survives(lambda p: p.mkdir(), expect_warning=True)

    def test_a_file_that_is_not_utf8_warns_instead_of_raising(self):
        self._assert_survives(lambda p: p.write_bytes(b"\xff\xfe\x00binaire"),
                              expect_warning=True)

    def test_a_broken_symlink_is_simply_absent(self):
        # exists() is False through a dangling link, so there is nothing to warn
        # about — it reads as "no .env", which is the ordinary case.
        self._assert_survives(lambda p: p.symlink_to("/inexistant"),
                              expect_warning=False)

    def test_no_file_at_all_is_the_ordinary_case(self):
        self._assert_survives(lambda p: None, expect_warning=False)

    def test_a_final_line_without_a_newline_is_still_read(self):
        out = self._import_config_beside(
            lambda p: p.write_text("DR_NEWPAPER_MODEL=sans-retour-final"))
        self.assertEqual(out.stdout.strip().splitlines()[-1], "sans-retour-final")

    def test_an_empty_value_falls_back_instead_of_shipping_a_blank(self):
        # DEFAULT_MODEL is the model name in the API payload, so a blank line
        # in .env must not become `model: ""`.
        for blank in ("DR_NEWPAPER_MODEL=\n", "DR_NEWPAPER_MODEL=   \n",
                      "DR_NEWPAPER_MODEL='  '\n"):
            with self.subTest(line=blank.strip()):
                out = self._import_config_beside(lambda p, b=blank: p.write_text(b))
                self.assertEqual(out.stdout.strip().splitlines()[-1], "MiniMax-M3")


class EmptyMeansUnsetTests(unittest.TestCase):
    """A variable exported empty must not mask the value in the file.

    `export MINIMAX_API_KEY=$SOMETHING_UNSET` in a wrapper leaves the name in the
    environment with nothing in it. Treating that as "already configured" would
    hide the real key sitting in .env, and nothing at the point of use tells a
    blanked variable from a missing one.
    """

    def _key_seen_with(self, exported):
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        for name in ("config.py", "minimax_client.py"):
            shutil.copy(REPO / name, workdir / name)
        (workdir / ".env").write_text("MINIMAX_API_KEY=sk-la-vraie-cle\n")
        env = {k: v for k, v in os.environ.items() if k != "MINIMAX_API_KEY"}
        if exported is not None:
            env["MINIMAX_API_KEY"] = exported
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import minimax_client; "
             "print(minimax_client._get_key())" % str(workdir)],
            capture_output=True, text=True, timeout=60, env=env, cwd=str(workdir))
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_an_empty_export_does_not_hide_the_key_in_the_file(self):
        self.assertEqual(self._key_seen_with(""), "sk-la-vraie-cle")

    def test_a_whitespace_export_does_not_either(self):
        self.assertEqual(self._key_seen_with("   "), "sk-la-vraie-cle")

    def test_a_real_export_still_wins(self):
        self.assertEqual(self._key_seen_with("sk-celle-exportee"), "sk-celle-exportee")

    def test_the_file_supplies_it_when_nothing_is_exported(self):
        self.assertEqual(self._key_seen_with(None), "sk-la-vraie-cle")


class SideEffectImportTests(unittest.TestCase):
    """telegram_sender reads its token from an import whose effect is the point.

    `import config` there looks unused and a tidy-up would remove it, leaving a
    module that silently finds no token. This is what would notice.
    """

    def test_the_token_reaches_telegram_sender_from_the_file_alone(self):
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        for name in ("config.py", "telegram_sender.py"):
            shutil.copy(REPO / name, workdir / name)
        (workdir / ".env").write_text("TELEGRAM_BOT_TOKEN=123456:DEPUIS-LE-FICHIER\n")
        env = {k: v for k, v in os.environ.items()
               if k not in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import telegram_sender; "
             "print(telegram_sender.BOT_TOKEN)" % str(workdir)],
            capture_output=True, text=True, timeout=60, env=env, cwd=str(workdir))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "123456:DEPUIS-LE-FICHIER")


if __name__ == "__main__":
    unittest.main()
