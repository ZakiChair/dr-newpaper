"""Shared configuration for Dr_NewPaper."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_DIR / "research_terminal.db"
DEFAULT_MODEL = os.getenv("DR_NEWPAPER_MODEL", "MiniMax-M3")
DEFAULT_LANG = os.getenv("DR_NEWPAPER_LANG", "fr")


def scihub_enabled(explicit: bool | None = None) -> bool:
    """Resolve whether Sci-Hub is an allowed PDF-download fallback, at call time.

    Sci-Hub is *opt-in*: an operator who configured nothing never reaches it.
    Downloading paywalled articles through it infringes copyright in most
    jurisdictions, so the deliberate act of setting the variable is what carries
    the operator's consent — a default-on fallback would make that choice for
    them. An explicit caller value still wins; otherwise Sci-Hub is OFF unless
    ``DR_NEWPAPER_ALLOW_SCIHUB`` is set to ``1``/``true``/``yes``/``on``.

    The check is an *allow*-list, not a deny-list: an unrecognised or empty
    value (a typo, a blank line in .env) leaves Sci-Hub off rather than
    silently enabling it. .strip() so a whitespace-padded opt-in from a docker
    --env-file / EnvironmentFile is still read, matching load_env_file.
    """
    if explicit is not None:
        return bool(explicit)
    return os.getenv("DR_NEWPAPER_ALLOW_SCIHUB", "0").strip().lower() in {"1", "true", "yes", "on"}


# Back-compat module constant — an *import-time snapshot* of the opt-in
# resolution. Prefer ``scihub_enabled()`` so a runtime opt-in is honoured.
ALLOW_SCIHUB = scihub_enabled()


def is_authorized(chat_id: object) -> bool:
    """Whether ``chat_id`` may drive the Telegram bot.

    Only the operator chat named by ``TELEGRAM_CHAT_ID`` is allowed. With the
    variable unset the answer is always False — the bot has no owner to serve,
    so it must serve nobody. Telegram bots are discoverable by name, and every
    command spends the operator's own MiniMax quota and network identity.

    A single id (not a list) on purpose: ``telegram_sender`` and ``pdf_sender``
    read the same variable as the *destination* chat, so a comma-separated
    value would silently break message delivery.

    Compared as numbers, not as text, to match ``bot.main()``: it parses the
    same variable with ``int()`` before handing it to ``filters.Chat``. Under a
    text comparison the two halves of this one allow-list disagree on spellings
    ``int()`` accepts — ``+123``, ``0123``, ``1_23`` — and the bot answers
    commands while refusing its own buttons, which reads as a Telegram glitch
    rather than a configuration mistake.
    """
    operator = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not operator:
        return False
    try:
        return int(chat_id) == int(operator)
    except (TypeError, ValueError):
        return False

# ── Canonical search defaults ───────────────────────────────────────────────
# One source of truth for "what a default search is", imported by every front
# end (Telegram bot, curses TUI, CLI) so the same query no longer yields
# different sources / counts depending on which interface you used.
#
# DEFAULT_SOURCES — the standard fast fan-out (metadata + abstracts).
# DEFAULT_DEEP_SOURCES — the set the deep (full-text + AI) pipeline actually
#   honours; passing more is silently ignored downstream, so this is the real
#   effective list for /deep and deep_research.
DEFAULT_SOURCES = ["pubmed", "crossref", "openalex"]
DEFAULT_DEEP_SOURCES = ["pubmed", "crossref", "openalex", "europe_pmc", "biorxiv", "medrxiv"]
DEFAULT_MAX_RESULTS = 5


def load_env_file(path: Path | None = None) -> None:
    """Load a simple .env file without overriding existing environment values."""
    env_path = path or PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
