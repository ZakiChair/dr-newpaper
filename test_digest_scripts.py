"""Tests for the cron entry points: daily_digest.sh / weekly_digest.sh / lib.sh.

These run the *real* scripts. A rewritten copy of their preamble would prove
nothing about the files cron actually invokes, which is where both defects lived:
the environment loader dropped part of any value containing a space, and the
`cd` landed in the wrong directory whenever the script was reached through a
symlink — the ordinary way a cron job is installed.

A fake repository is assembled in a temp directory (the real scripts, a stub
`run.py`, a `.env`) so nothing here touches the operator's own checkout or the
network.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
BASH = shutil.which("bash")

needs_bash = unittest.skipUnless(BASH, "bash is required to run the digest scripts")


class _FakeRepo:
    """A throwaway copy of the repo's shell entry points plus a stub run.py."""

    def __init__(self, env_text=None, scripts=("daily_digest.sh", "lib.sh")):
        # Resolved: on macOS the temp root is /var, a symlink to /private/var,
        # and the script's own getcwd() reports the resolved form.
        self.dir = Path(tempfile.mkdtemp()).resolve()
        for name in scripts:
            shutil.copy(REPO / name, self.dir / name)
            (self.dir / name).chmod(0o755)
        # Records the working directory it was called from, then stops the run
        # before any real search happens.
        (self.dir / "run.py").write_text(
            "import os, sys\n"
            "open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cwd.txt'),"
            " 'w').write(os.getcwd())\n"
            "sys.exit(0)\n"
        )
        if env_text is not None:
            (self.dir / ".env").write_text(env_text)

    def run(self, script=None, cwd=None, extra_env=None):
        env = dict(os.environ)
        env.pop("DR_NEWPAPER_MODEL", None)
        env.update(extra_env or {})
        return subprocess.run(
            [str(script or self.dir / "daily_digest.sh")],
            capture_output=True, text=True, timeout=60,
            cwd=str(cwd or Path(tempfile.gettempdir())), env=env,
        )

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


@needs_bash
class EnvLoadingTests(unittest.TestCase):
    """`export $(cat .env | xargs)` split every value on whitespace."""

    def _load(self, env_text, preset=None):
        """Source lib.sh in a fake repo and dump the environment it produced."""
        repo = _FakeRepo(env_text=env_text, scripts=("lib.sh",))
        self.addCleanup(repo.cleanup)
        script = (
            f'cd "{repo.dir}" && . ./lib.sh && load_env && '
            'python3 -c "import os,json;print(json.dumps(dict(os.environ)))"'
        )
        # A clean environment: the loader leaves already-exported values alone,
        # so an inherited one would silently decide the assertion.
        env = {k: v for k, v in os.environ.items() if not k.startswith("DR_NEWPAPER_")
               and not k.startswith("TELEGRAM_") and not k.startswith("MINIMAX_")}
        env.update(preset or {})
        out = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                             timeout=60, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_an_already_exported_value_is_left_alone(self):
        env = self._load("TELEGRAM_CHAT_ID=4242\n", preset={"TELEGRAM_CHAT_ID": "999"})
        self.assertEqual(env.get("TELEGRAM_CHAT_ID"), "999")

    def test_an_empty_export_does_not_count_as_a_value(self):
        # Same rule as config.load_env_file: `export KEY=$UNSET` must not mask
        # the real value in the file. Divergence here would be a silent one.
        for blank in ("", "   "):
            with self.subTest(exported=repr(blank)):
                env = self._load("TELEGRAM_CHAT_ID=4242\n",
                                 preset={"TELEGRAM_CHAT_ID": blank})
                self.assertEqual(env.get("TELEGRAM_CHAT_ID"), "4242")

    def test_a_value_named_like_one_of_the_loader_locals_still_loads(self):
        # The existence check must look at the environment, not at the shell:
        # load_env has locals named file/line/key/value.
        env = self._load("line=une-valeur\nvalue=une-autre\n")
        self.assertEqual(env.get("line"), "une-valeur")
        self.assertEqual(env.get("value"), "une-autre")

    def test_a_value_containing_spaces_survives_whole(self):
        env = self._load("DR_NEWPAPER_MODEL=MiniMax M3 Pro\n")
        self.assertEqual(env.get("DR_NEWPAPER_MODEL"), "MiniMax M3 Pro")

    def test_comments_and_blank_lines_are_ignored(self):
        env = self._load("# un commentaire\n\n  # indenté\nTELEGRAM_BOT_TOKEN=abc\n")
        self.assertEqual(env.get("TELEGRAM_BOT_TOKEN"), "abc")

    def test_surrounding_whitespace_is_trimmed_like_the_python_loaders(self):
        # bot.py and pdf_sender.py both .strip() key and value; the shell path
        # must agree, or the same .env means two different things.
        env = self._load("  TELEGRAM_CHAT_ID = 4242 \n")
        self.assertEqual(env.get("TELEGRAM_CHAT_ID"), "4242")

    def test_a_value_is_never_executed_as_a_command(self):
        # Sourcing the file would run this; the loader must only assign.
        env = self._load("MINIMAX_API_KEY=$(echo compromis)\n")
        self.assertEqual(env.get("MINIMAX_API_KEY"), "$(echo compromis)")

    def test_a_value_containing_an_equals_sign_keeps_it(self):
        env = self._load("MINIMAX_API_KEY=abc==\n")
        self.assertEqual(env.get("MINIMAX_API_KEY"), "abc==")


@needs_bash
class ScriptLocationTests(unittest.TestCase):
    """The script must run from the repo it belongs to, however it was reached."""

    def test_it_runs_from_its_own_directory(self):
        repo = _FakeRepo(env_text="TELEGRAM_BOT_TOKEN=abc\n")
        self.addCleanup(repo.cleanup)
        result = repo.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((repo.dir / "cwd.txt").read_text(), str(repo.dir))

    def test_it_runs_from_the_repo_when_invoked_through_a_symlink(self):
        # How a cron job is normally installed:
        #   ln -s ~/dr-newpaper/daily_digest.sh /usr/local/bin/daily_digest.sh
        repo = _FakeRepo(env_text="TELEGRAM_BOT_TOKEN=abc\n")
        self.addCleanup(repo.cleanup)
        bindir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, bindir, True)
        link = bindir / "daily_digest.sh"
        link.symlink_to(repo.dir / "daily_digest.sh")

        result = repo.run(script=link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((repo.dir / "cwd.txt").read_text(), str(repo.dir))

    def test_it_follows_a_chain_of_symlinks(self):
        repo = _FakeRepo(env_text="TELEGRAM_BOT_TOKEN=abc\n")
        self.addCleanup(repo.cleanup)
        first = Path(tempfile.mkdtemp())
        second = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, first, True)
        self.addCleanup(shutil.rmtree, second, True)
        (first / "daily_digest.sh").symlink_to(repo.dir / "daily_digest.sh")
        (second / "daily_digest.sh").symlink_to(first / "daily_digest.sh")

        result = repo.run(script=second / "daily_digest.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((repo.dir / "cwd.txt").read_text(), str(repo.dir))


@needs_bash
class MissingEnvTests(unittest.TestCase):
    """`set -e` does not catch a failing command substitution."""

    def test_a_missing_env_stops_the_run(self):
        repo = _FakeRepo(env_text=None)
        self.addCleanup(repo.cleanup)
        result = repo.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".env", result.stderr)
        self.assertFalse((repo.dir / "cwd.txt").exists(),
                         "the digest ran without credentials")

    def test_the_refusal_names_the_directory_it_searched(self):
        # Aborting is not the point — a failed redirection would do that on its
        # own. The point is the message: when a cron run stops, the operator
        # needs to know *where* the script looked, since landing in the wrong
        # directory is the failure this whole preamble exists to prevent.
        repo = _FakeRepo(env_text=None)
        self.addCleanup(repo.cleanup)
        result = repo.run()
        self.assertIn(str(repo.dir), result.stderr)


@needs_bash
class CrossReaderAgreementTests(unittest.TestCase):
    """The shell and the Python loaders must read one file the same way.

    They are two implementations of one format, and the file they read holds the
    credentials, so a disagreement is not cosmetic: before, `xargs` stripped the
    quotes around a value while the Python side kept them, which is why a
    quoted TELEGRAM_CHAT_ID delivered digests from cron and stopped the bot dead.
    """

    FIXTURE = "\n".join([
        "# un commentaire",
        "",
        "TELEGRAM_BOT_TOKEN=123456:ABC-DEF",
        'TELEGRAM_CHAT_ID="4242"',
        "DR_NEWPAPER_MODEL='MiniMax M3 Pro'",
        "  DR_NEWPAPER_LANG = fr ",
        "MINIMAX_API_KEY=abc==",
        "AVEC_DIESE=valeur#pas-un-commentaire",
        # Not assignments: a .env written to be sourced, and a name with a space.
        "export IGNORE_MOI=1",
        "NOM INVALIDE=2",
    ]) + "\n"

    KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DR_NEWPAPER_MODEL",
            "DR_NEWPAPER_LANG", "MINIMAX_API_KEY", "AVEC_DIESE",
            "IGNORE_MOI", "exportIGNORE_MOI")

    def setUp(self):
        # config.py is copied in, not imported from the real repo: importing it
        # loads the .env sitting next to it, so the operator's own file would
        # win over this fixture — and, precedence being what it is, silently.
        self.repo = _FakeRepo(env_text=self.FIXTURE, scripts=("lib.sh", "config.py"))
        self.addCleanup(self.repo.cleanup)

    def _clean_env(self):
        return {k: v for k, v in os.environ.items() if k not in self.KEYS}

    def _read_with_shell(self):
        keys = " ".join(self.KEYS)
        script = (
            f'cd "{self.repo.dir}" && . ./lib.sh && load_env && '
            f'python3 -c "import os,json,sys;print(json.dumps({{k:os.environ.get(k) '
            f'for k in \'{keys}\'.split()}}))"'
        )
        out = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                             timeout=60, env=self._clean_env())
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def _read_with_python(self):
        # No explicit path: importing the copied config reads the .env beside it,
        # which is the same default path load_env takes on the shell side.
        script = (
            "import json, os, sys; sys.path.insert(0, %r); import config; "
            "print(json.dumps({k: os.environ.get(k) for k in %r}))"
            % (str(self.repo.dir), list(self.KEYS))
        )
        out = subprocess.run(["python3", "-c", script], capture_output=True, text=True,
                             timeout=60, env=self._clean_env())
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_both_readers_produce_the_same_environment(self):
        self.assertEqual(self._read_with_shell(), self._read_with_python())

    def test_the_agreed_reading_is_the_expected_one(self):
        # Agreement alone would be satisfied by two identically wrong readers.
        self.assertEqual(self._read_with_python(), {
            "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF",
            "TELEGRAM_CHAT_ID": "4242",              # quotes removed
            "DR_NEWPAPER_MODEL": "MiniMax M3 Pro",   # kept whole, quotes removed
            "DR_NEWPAPER_LANG": "fr",                # trimmed
            "MINIMAX_API_KEY": "abc==",              # split on the first = only
            "AVEC_DIESE": "valeur#pas-un-commentaire",
            "IGNORE_MOI": None,                      # `export FOO=…` is not an
            "exportIGNORE_MOI": None,                # assignment, under any name
        })


class BothScriptsAgreeTests(unittest.TestCase):
    """The weekly script must not drift from the daily one it was copied from."""

    def test_both_scripts_share_the_same_preamble(self):
        daily = (REPO / "daily_digest.sh").read_text().splitlines()
        weekly = (REPO / "weekly_digest.sh").read_text().splitlines()
        marker = "load_env"
        self.assertIn(marker, daily)
        self.assertIn(marker, weekly)
        # Everything up to and including load_env is the shared bootstrap.
        self.assertEqual(daily[2:daily.index(marker) + 1],
                         weekly[2:weekly.index(marker) + 1])


if __name__ == "__main__":
    unittest.main()
