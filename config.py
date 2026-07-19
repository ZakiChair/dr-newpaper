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

    Sci-Hub is the *always-on* download fallback: it is independent of the
    search depth (the deepsearch knob only governs the per-article MiniMax AI
    analysis — summaries and their critical appraisal). An explicit caller value
    always wins; otherwise Sci-Hub is ON unless an operator opts out by setting
    ``DR_NEWPAPER_ALLOW_SCIHUB`` to ``0``/``false``/``no``/``off``.
    """
    if explicit is not None:
        return bool(explicit)
    # .strip() so a whitespace-padded operator opt-out (e.g. a stray trailing
    # space in a docker --env-file / EnvironmentFile) still disables Sci-Hub —
    # the kill-switch must fail *safe*, matching load_env_file's own stripping.
    return os.getenv("DR_NEWPAPER_ALLOW_SCIHUB", "1").strip().lower() not in {"0", "false", "no", "off"}


# Back-compat module constant — now an *import-time snapshot* of the default-on
# resolution. Prefer ``scihub_enabled()`` so a runtime opt-out is honoured.
ALLOW_SCIHUB = scihub_enabled()

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
