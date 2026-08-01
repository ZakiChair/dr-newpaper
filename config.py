"""Shared configuration for Dr_NewPaper."""
from __future__ import annotations

import os
import re
from pathlib import Path

# What an environment variable may be named. A line whose left-hand side is not
# one is not an assignment, whatever it looks like.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PROJECT_DIR / "research_terminal.db"

# The one library root. Anchored to this file, never to the working directory:
# the CLI and the TUI each used to build a relative path of their own, so running
# them from elsewhere filed exports next to the shell while the articles they
# describe stayed in the repo. Every front end imports this instead.
DOSSIER_BASE = PROJECT_DIR / "Dossier"
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


def parse_env_line(line: str) -> "tuple[str, str] | None":
    """Parse one ``.env`` line into ``(name, value)``, or None if it carries none.

    The definition of the format, shared with ``lib.sh``'s ``load_env`` so the
    cron scripts and the Python entry points read one file the same way. They
    used to differ: ``export $(cat .env | xargs)`` stripped the quotes around a
    value and split it on spaces, while the Python side did neither, so a
    ``TELEGRAM_CHAT_ID="123"`` delivered digests from cron and stopped the bot.

    Blank lines and ``#`` comments yield None. The split is on the first ``=``
    only, so a value may contain more. Whitespace around both halves is dropped,
    then one matching pair of surrounding quotes, which is what writing a value
    with spaces in a ``.env`` normally looks like. A ``#`` inside a value is part
    of the value: only a whole-line comment is a comment.
    """
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    name, value = line.split("=", 1)
    name, value = name.strip(), value.strip()
    # `export FOO=bar` — a .env written to be sourced — is not an assignment to
    # a variable called "export FOO". Skipping it is the only reading the shell
    # can share: trimming alone left this side with a name holding a space and
    # the shell side with "exportFOO", which is a valid name and silently wrong.
    if not _ENV_NAME.fullmatch(name):
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return name, value


def allowed_user_ids() -> set[int]:
    """The people allowed to drive the bot from a shared chat.

    Read from ``TELEGRAM_ALLOWED_USERS`` (comma-separated Telegram user ids).
    Only consulted when the operator chat is a group or channel — see
    :func:`is_authorized`. Unset means the empty set, never "everyone".

    Unparsable entries are dropped rather than trusted: dropping one can only
    take access away. A wholly malformed list therefore collapses to empty,
    which ``bot.main()`` refuses to start on, so the mistake surfaces at
    startup instead of granting anyone anything.
    """
    raw = os.getenv("TELEGRAM_ALLOWED_USERS", "")
    users = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            user = int(entry)
        except ValueError:
            continue
        # Telegram user ids are strictly positive, so 0 or a negative value is
        # provably not a person — most likely a chat id pasted into the wrong
        # variable. Keeping it would let an empty-looking list read as filled.
        if user > 0:
            users.add(user)
    return users


def is_authorized(chat_id: object, user_id: object = None) -> bool:
    """Whether an update from ``chat_id``, sent by ``user_id``, may drive the bot.

    Only the operator chat named by ``TELEGRAM_CHAT_ID`` is allowed. With the
    variable unset the answer is always False — the bot has no owner to serve,
    so it must serve nobody. Telegram bots are discoverable by name, and every
    command spends the operator's own MiniMax quota and network identity.

    A chat is a *place*, and a group is a place other people can be let into,
    so matching the chat alone is only sufficient where the place holds one
    person. Telegram gives a private chat the user's own id, so a positive
    ``TELEGRAM_CHAT_ID`` already identifies exactly one human and needs nothing
    more. Group, supergroup and channel ids are negative; there the sender must
    also appear in :func:`allowed_user_ids`, which is empty until an operator
    fills it — a group whose member list a third party can extend must not
    inherit the bot.

    A single chat id (not a list) on purpose: ``telegram_sender`` and
    ``pdf_sender`` read the same variable as the *destination* chat, so a
    comma-separated value would silently break message delivery.

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
        operator_chat = int(operator)
        if int(chat_id) != operator_chat:
            return False
    except (TypeError, ValueError):
        return False
    if operator_chat > 0:
        return True
    try:
        return int(user_id) in allowed_user_ids()
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


def load_env_file(path: "str | Path | None" = None) -> None:
    """Load a ``.env`` file without overriding existing environment values.

    The single entry point for reading ``.env`` — bot.py, pdf_sender.py and
    send_digest.py each carried their own copy of this loop, which is how they
    came to disagree with the cron scripts about what a line means. Line syntax
    lives in :func:`parse_env_line`, mirrored by ``lib.sh``.

    An already-exported value wins, matching python-dotenv and ``lib.sh``: an
    operator running ``TELEGRAM_CHAT_ID=999 ./daily_digest.sh`` to try something
    out means it, and a file should not overrule what they just typed.
    """
    env_path = Path(path) if path else PROJECT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        parsed = parse_env_line(line)
        if parsed and parsed[0] not in os.environ:
            os.environ[parsed[0]] = parsed[1]
