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


if __name__ == "__main__":
    unittest.main()
