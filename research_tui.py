#!/usr/bin/env python3
"""Interactive terminal UI for Dr_NewPaper Research Desk.

Uses only Python stdlib curses so it works on a bare server. Launch with:
    python3 research_tui.py

Keys:
    ↑/↓ or j/k  move article selection
    tab         switch focus between articles/watchlists
    s           search articles from inside the terminal
    d           search recent articles in a research domain
    m           run a meta-analysis on a query
    o           open the animated search-configuration screen
    t           follow a topic / create watchlist
    a           evaluate/rescore selected article
    p           download the selected study's PDF
    r           reload database
    e           export current filtered dossier
    x           delete the selected saved study
    z           clear the saved library
    c / C       cycle color theme forward / backward
    q           quit

Any blocking operation (search, meta-analysis, watchlist refresh, PDF download,
export) runs in a background thread while the main loop animates a Claude-style
search spinner — a playful verb, a bouncing beam and a sweeping bar — so
the UI never freezes. Preview the animation frames with `--spin-demo`.
"""
from __future__ import annotations

import argparse
import contextlib
import curses
import inspect
import io
import json
import random
import re
import textwrap
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import DEFAULT_DB_PATH, DEFAULT_DEEP_SOURCES, DEFAULT_LANG, DEFAULT_MAX_RESULTS, DEFAULT_SOURCES, scihub_enabled
from storage import ResearchStore
import research_exports
import scoring
import meta_analysis
import pdf_sender
from research_terminal import ResearchArticle, run_search


APP_VERSION = "v0.4.0-research-desk"


COLOR_NAME_TO_CURSES = {
    "black": curses.COLOR_BLACK,
    "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN,
    "yellow": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE,
    "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN,
    "white": curses.COLOR_WHITE,
}

# 256-colour indices used when the terminal supports them (else the 8-colour
# fallback in each theme is used). Lets us hit real Bloomberg amber, neon, sepia…
C256 = {
    "amber": 214, "orange": 208, "gold": 220, "neon_pink": 198, "neon_cyan": 51,
    "neon_purple": 141, "sepia": 180, "parchment": 223, "ink": 137, "rust": 130,
    "deep_blue": 26, "teal": 37, "aqua": 45, "sea": 30, "phosphor": 46, "lime": 118,
    "grey": 245, "slate": 102,
    "clay": 173, "coral": 209, "cream": 230, "fog": 250,
}

# Front/box frame character sets — each theme picks one so the framing itself
# changes with the art direction.
BORDER_STYLES = {
    "round":  {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│", "ml": "├", "mr": "┤"},
    "double": {"tl": "╔", "tr": "╗", "bl": "╚", "br": "╝", "h": "═", "v": "║", "ml": "╠", "mr": "╣"},
    "heavy":  {"tl": "┏", "tr": "┓", "bl": "┗", "br": "┛", "h": "━", "v": "┃", "ml": "┣", "mr": "┫"},
    "ascii":  {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "=", "v": "|", "ml": "+", "mr": "+"},
    "dash":   {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "╌", "v": "╎", "ml": "├", "mr": "┤"},
    "block":  {"tl": "▛", "tr": "▜", "bl": "▙", "br": "▟", "h": "▀", "v": "▌", "ml": "▌", "mr": "▐"},
}


def border_style(name: str) -> dict:
    return BORDER_STYLES.get(name or "round", BORDER_STYLES["round"])


# Wordmark fonts for "DR · NP" — chosen per theme so the logo type changes too.
LOGO_FONTS = {
    "ansishadow": {  # tall heavy block (ANSI Shadow) — "DR · NEWPAPER" fills the bar
        "dr": ["██████╗ ██████╗", "██╔══██╗██╔══██╗", "██║  ██║██████╔╝",
               "██║  ██║██╔══██╗", "██████╔╝██║  ██║", "╚═════╝ ╚═╝  ╚═╝"],
        "np": ['███╗   ██╗███████╗██╗    ██╗██████╗  █████╗ ██████╗ ███████╗██████╗ ',
               '████╗  ██║██╔════╝██║    ██║██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔══██╗',
               '██╔██╗ ██║█████╗  ██║ █╗ ██║██████╔╝███████║██████╔╝█████╗  ██████╔╝',
               '██║╚██╗██║██╔══╝  ██║███╗██║██╔═══╝ ██╔══██║██╔═══╝ ██╔══╝  ██╔══██╗',
               '██║ ╚████║███████╗╚███╔███╔╝██║     ██║  ██║██║     ███████╗██║  ██║',
               '╚═╝  ╚═══╝╚══════╝ ╚══╝╚══╝ ╚═╝     ╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝'],
        "dot": ["  ", "  ", "  ", "  ", "██", "██"],
    },
    "miniblock": {  # compact retro 3-row slab
        "dr": ["█▀▙ █▀▙", "█ █ █▀▖", "█▄▟ ▀ ▀"],
        "np": ["█▖ █ █▀▙", "█▝▖█ █▀▘", "▀ ▝▀ ▀  "],
        "dot": ["  ", "  ", "▄▄"],
    },
}


THEMES = {
    "bloomberg": {
        "name": "bloomberg",
        "accent_name": "amber terminal",
        "border": "double",
        "border_box": "double",
        "logo_font": "ansishadow",
        "logo_fill": "█",
        "front_palette": {"brand": "yellow", "emblem": "white", "meta": "yellow",
                          "controls": "yellow", "mission": "white", "texture": "yellow",
                          "border": "yellow", "background": "default"},
        "front_palette256": {"brand": C256["amber"], "emblem": 253, "meta": C256["gold"],
                             "controls": C256["orange"], "mission": 250, "texture": C256["rust"],
                             "border": C256["amber"]},
        "primary": curses.COLOR_YELLOW, "secondary": curses.COLOR_WHITE, "title": curses.COLOR_YELLOW,
        "status": curses.COLOR_WHITE, "danger": curses.COLOR_RED, "muted": curses.COLOR_YELLOW,
        "colors256": {"primary": C256["amber"], "secondary": 253, "title": C256["gold"],
                      "status": 231, "danger": 196, "muted": C256["rust"]},
        "glyph": "◆", "sparkle": "✳",
        "spinner": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
        "banner": "MARKET INTELLIGENCE",
        "empty": "No live tape yet — press S to search, Tab for saved library.",
        "frame_style": "market_terminal",
        "mascot_name": "Quill Terminal",
        "tagline": "Live evidence ticker for the trading floor of science",
        "mascot": [
            "   ╭┈┈◆┈┈╮      ",
            "  ╭▛▀▀▀▀▀▜╮     ",
            "  ▌▓◕▓▓◕▓▐      ",
            "  ▌▓▓‿‿▓▓▐      ",
            "  ╰▛▄📄▄▜╯       ",
            "   ▝▀▘ ▝▀▘      ",
        ],
        "texture": "▌▐▌▐ ticker tape ▐▌▐▌",
    },
    "matrix": {
        "name": "matrix",
        "accent_name": "phosphor green",
        "border": "heavy",
        "border_box": "heavy",
        "logo_font": "ansishadow",
        "logo_fill": "▓",
        "front_palette": {"brand": "green", "emblem": "green", "meta": "green",
                          "controls": "green", "mission": "green", "texture": "green",
                          "border": "green", "background": "default"},
        "front_palette256": {"brand": C256["phosphor"], "emblem": 82, "meta": C256["phosphor"],
                             "controls": 40, "mission": 120, "texture": 28, "border": C256["phosphor"]},
        "primary": curses.COLOR_GREEN, "secondary": curses.COLOR_GREEN, "title": curses.COLOR_GREEN,
        "status": curses.COLOR_GREEN, "danger": curses.COLOR_RED, "muted": curses.COLOR_GREEN,
        "colors256": {"primary": C256["phosphor"], "secondary": 40, "title": 82,
                      "status": 120, "danger": 196, "muted": 28},
        "glyph": "▣", "sparkle": "✦",
        "spinner": ["⠷", "⠯", "⠟", "⠻", "⠽", "⠾"],
        "banner": "WAKE UP, RESEARCHER",
        "empty": "No signal in the stream — press S to jack into a query.",
        "frame_style": "digital_rain",
        "mascot_name": "Monolith Lynx",
        "tagline": "Jack into the literature stream",
        "mascot": [
            "  ╭╲▁▁▁▁╱╮      ",
            "  ▟▒◉▒▒◉▒▙      ",
            "  ▌▒▒▒▿▒▒▐      ",
            "  ▌▒1📄0▒▐       ",
            "  ╰▀▀▀▀▀▀╯      ",
            "   ╚╝  ╚╝       ",
        ],
        "texture": "010011 ░▒▓ evidence stream ▓▒░ 110010",
    },
    "cute": {
        "name": "cute",
        "accent_name": "soft pink",
        "border": "round",
        "border_box": "round",
        "logo_font": "ansishadow",
        "logo_fill": "█",
        "front_palette": {"brand": "magenta", "emblem": "magenta", "meta": "magenta",
                          "controls": "yellow", "mission": "white", "texture": "magenta",
                          "border": "magenta", "background": "default"},
        "front_palette256": {"brand": 211, "emblem": 205, "meta": 213, "controls": 222,
                             "mission": 255, "texture": 218, "border": 211},
        "primary": curses.COLOR_MAGENTA, "secondary": curses.COLOR_CYAN, "title": curses.COLOR_MAGENTA,
        "status": curses.COLOR_YELLOW, "danger": curses.COLOR_RED, "muted": curses.COLOR_MAGENTA,
        "colors256": {"primary": 211, "secondary": 87, "title": 213, "status": 228, "danger": 203, "muted": 218},
        "glyph": "♡", "sparkle": "♡",
        "spinner": ["✿", "❀", "✾", "❁", "✽", "❃"],
        "banner": "COZY PAPER GARDEN",
        "empty": "No little papers here yet — press S and grow a search 🌱",
        "frame_style": "pastel_lab",
        "mascot_name": "Paper Mochi",
        "tagline": "A cozy garden where good papers bloom",
        "mascot": [
            "   ╭▔▔▔▔▔╮      ",
            "  ╭░●░░░●░╮     ",
            "  ▌░♥░ᵕ░♥░▐     ",
            "  ▌░░╰📄╯░░▐     ",
            "   ╰▁▁▁▁▁╯      ",
            "    ⌣   ⌣       ",
        ],
        "texture": "♡ ⋆｡˚ soft evidence garden ˚｡⋆ ♡",
    },
    "synthwave": {
        "name": "synthwave",
        "accent_name": "neon outrun",
        "border": "block",
        "border_box": "double",
        "logo_font": "ansishadow",
        "logo_fill": "█",
        "front_palette": {"brand": "magenta", "emblem": "cyan", "meta": "magenta",
                          "controls": "cyan", "mission": "white", "texture": "magenta",
                          "border": "magenta", "background": "default"},
        "front_palette256": {"brand": C256["neon_pink"], "emblem": C256["neon_cyan"], "meta": 171,
                             "controls": 45, "mission": 255, "texture": 93, "border": C256["neon_pink"]},
        "primary": curses.COLOR_MAGENTA, "secondary": curses.COLOR_CYAN, "title": curses.COLOR_MAGENTA,
        "status": curses.COLOR_CYAN, "danger": curses.COLOR_RED, "muted": curses.COLOR_MAGENTA,
        "colors256": {"primary": C256["neon_pink"], "secondary": C256["neon_cyan"], "title": 171,
                      "status": 87, "danger": 197, "muted": 57},
        "glyph": "▰", "sparkle": "✺",
        "spinner": ["◜", "◠", "◝", "◞", "◡", "◟"],
        "banner": "NEON ARCHIVE",
        "empty": "Empty grid — press S to cruise the literature at 88 mph.",
        "frame_style": "outrun_grid",
        "mascot_name": "Vex Synth",
        "tagline": "Cruise the literature at 88 mph",
        "mascot": [
            "   ╭━◇━◇━╮      ",
            "  ╭█▀▀▀▀▀█╮     ",
            "  ▌░✦░░✦░▐      ",
            "  ▌░░‿‿░░▐      ",
            "  ╰█▄📄▄█╯       ",
            "   ╲▂▂▂╱        ",
        ],
        "texture": "▞▚▞▚ sunset grid ▞▚ outrun ▚▞▚▞",
    },
    "sepia": {
        "name": "sepia",
        "accent_name": "manuscript sepia",
        "border": "ascii",
        "border_box": "ascii",
        "logo_font": "ansishadow",
        "logo_fill": "▒",
        "front_palette": {"brand": "yellow", "emblem": "yellow", "meta": "yellow",
                          "controls": "white", "mission": "white", "texture": "yellow",
                          "border": "yellow", "background": "default"},
        "front_palette256": {"brand": C256["sepia"], "emblem": C256["rust"], "meta": C256["ink"],
                             "controls": C256["sepia"], "mission": C256["parchment"],
                             "texture": C256["ink"], "border": C256["sepia"]},
        "primary": curses.COLOR_YELLOW, "secondary": curses.COLOR_WHITE, "title": curses.COLOR_YELLOW,
        "status": curses.COLOR_WHITE, "danger": curses.COLOR_RED, "muted": curses.COLOR_WHITE,
        "colors256": {"primary": C256["sepia"], "secondary": C256["ink"], "title": C256["rust"],
                      "status": C256["parchment"], "danger": 124, "muted": C256["slate"]},
        "glyph": "§", "sparkle": "✶",
        "spinner": ["·", "‥", "…", "‥"],
        "banner": "THE DAILY LEDGER",
        "empty": "Blank column — press S to set today's headline.",
        "frame_style": "broadsheet",
        "mascot_name": "Scriba Owl",
        "tagline": "All the science that's fit to print",
        "mascot": [
            "   ╭⌐▤▤⌐╮       ",
            "  ╭▒▒▒▒▒▒╮      ",
            "  ▌▒◔▒▒◔▒▐      ",
            "  ▌▒▒╰▾╯▒▐      ",
            "  ╰▒▒📄▒▒╯       ",
            "    ⩊  ⩊        ",
        ],
        "texture": "─◦─ broadsheet column ─◦─ folio ─◦─",
    },
    "ocean": {
        "name": "ocean",
        "accent_name": "deep current",
        "border": "heavy",
        "border_box": "heavy",
        "logo_font": "ansishadow",
        "logo_fill": "█",
        "front_palette": {"brand": "cyan", "emblem": "cyan", "meta": "cyan",
                          "controls": "blue", "mission": "white", "texture": "cyan",
                          "border": "cyan", "background": "default"},
        "front_palette256": {"brand": C256["aqua"], "emblem": C256["neon_cyan"], "meta": 39,
                             "controls": C256["teal"], "mission": 195, "texture": C256["sea"],
                             "border": C256["aqua"]},
        "primary": curses.COLOR_CYAN, "secondary": curses.COLOR_BLUE, "title": curses.COLOR_CYAN,
        "status": curses.COLOR_CYAN, "danger": curses.COLOR_RED, "muted": curses.COLOR_BLUE,
        "colors256": {"primary": C256["aqua"], "secondary": C256["teal"], "title": C256["neon_cyan"],
                      "status": 123, "danger": 203, "muted": C256["deep_blue"]},
        "glyph": "≈", "sparkle": "❉",
        "spinner": ["▁", "▃", "▅", "▆", "▇", "▆", "▅", "▃"],
        "banner": "ABYSSAL INDEX",
        "empty": "Still waters — press S to dive into a query.",
        "frame_style": "deep_sea",
        "mascot_name": "Bloop Jelly",
        "tagline": "Dive the depths of the literature",
        "mascot": [
            "   ╭▂▂▂▂▂╮      ",
            "  ╭▓◕▓▓◕▓╮      ",
            "  ▌▓▓◡◡▓▓▐      ",
            "  ▌▓░📄░▓▐       ",
            "   ╰┬┬┬┬╯       ",
            "   ╰╯╰╯╰╯       ",
        ],
        "texture": "≈≋≈ tidal current ≈≋ abyss ≋≈≋≈",
    },
    "claude": {
        "name": "claude",
        "accent_name": "terracotta",
        "border": "round",
        "border_box": "round",
        "logo_font": "ansishadow",
        "logo_fill": "█",
        "front_palette": {"brand": "red", "emblem": "white", "meta": "white",
                          "controls": "red", "mission": "white", "texture": "white",
                          "border": "red", "background": "default"},
        "front_palette256": {"brand": C256["clay"], "emblem": C256["cream"], "meta": C256["fog"],
                             "controls": C256["coral"], "mission": C256["cream"],
                             "texture": C256["rust"], "border": C256["clay"]},
        "primary": curses.COLOR_RED, "secondary": curses.COLOR_WHITE, "title": curses.COLOR_RED,
        "status": curses.COLOR_WHITE, "danger": curses.COLOR_RED, "muted": curses.COLOR_WHITE,
        "colors256": {"primary": C256["clay"], "secondary": C256["cream"], "title": C256["coral"],
                      "status": C256["fog"], "danger": 196, "muted": C256["grey"]},
        "glyph": "✳", "sparkle": "✦",
        "spinner": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
        "banner": "THOUGHTFUL SYNTHESIS",
        "empty": "A clean desk — press S to think through a question.",
        "frame_style": "warm_dark",
        "mascot_name": "Clay Ember",
        "tagline": "A calm, careful companion for the literature",
        "mascot": [
            "   ╭─✳──✳─╮    ",
            "  ╭▛▀▀▀▀▀▜╮    ",
            "  ▌·◕··◕·▐     ",
            "  ▌··‿‿··▐     ",
            "  ╰▙▄📄▄▟╯     ",
            "   ▝▀▘ ▝▀▘     ",
        ],
        "texture": "✳ ⋅ thoughtful evidence desk ⋅ ✳",
    },
}


THEME_ORDER = ["bloomberg", "matrix", "cute", "synthwave", "sepia", "ocean", "claude"]


def theme_config(name: str) -> dict:
    return dict(THEMES.get((name or "").lower(), THEMES["bloomberg"]))


def _has_256() -> bool:
    try:
        return curses.COLORS >= 256
    except Exception:
        return False


def _palette_color(value: str | int | None, fallback: int = curses.COLOR_WHITE) -> int:
    if isinstance(value, int):
        # 256-colour index only when the terminal can render it, else fall back.
        return value if (value < 8 or _has_256()) else fallback
    if not value or value == "default":
        return fallback
    return COLOR_NAME_TO_CURSES.get(str(value).lower(), fallback)


def _theme_color(cfg: dict, role: str) -> int:
    """Resolve a main-pair colour, preferring the theme's 256-colour value."""
    c256 = (cfg.get("colors256") or {}).get(role)
    if c256 is not None and _has_256():
        return c256
    return cfg[role]


def _front_color(palette: dict, fp256: dict, role: str) -> int:
    if fp256 and role in fp256 and _has_256():
        return fp256[role]
    return _palette_color(palette.get(role))


def _apply_theme_pairs(cfg: dict) -> None:
    """(Re)initialise the six main colour pairs for the active theme."""
    curses.init_pair(1, _theme_color(cfg, "secondary"), -1)
    curses.init_pair(2, _theme_color(cfg, "primary"), -1)
    curses.init_pair(3, _theme_color(cfg, "title"), -1)
    curses.init_pair(4, _theme_color(cfg, "status"), -1)
    curses.init_pair(5, _theme_color(cfg, "danger"), -1)
    curses.init_pair(6, _theme_color(cfg, "muted"), -1)


def theme_front_palette(name: str) -> dict[str, str]:
    """Color roles used only by the front/logo block."""
    cfg = theme_config(name)
    palette = dict(cfg.get("front_palette") or {})
    defaults = {
        "brand": "white",
        "emblem": "white",
        "meta": "white",
        "controls": "white",
        "mission": "white",
        "texture": "white",
        "border": "white",
        "background": "default",
    }
    defaults.update(palette)
    return defaults


def cycle_theme(state: "TuiState", direction: int = 1) -> str:
    """Cycle theme in a deterministic order. Returns the newly active theme."""
    current = theme_config(state.theme)["name"]
    try:
        idx = THEME_ORDER.index(current)
    except ValueError:
        idx = 0
    state.theme = THEME_ORDER[(idx + direction) % len(THEME_ORDER)]
    cfg = theme_config(state.theme)
    state.status = f"Theme switched → {cfg['name']} ({cfg['accent_name']})"
    return state.theme


# Claude-style "fun verb" pool — English gerunds to match the rest of the UI copy.
# They read as 'Investigating "query"…'.
RESEARCH_VERBS = [
    "Investigating", "Pondering", "Cogitating", "Sleuthing", "Synthesizing",
    "Cross-referencing", "Distilling", "Foraging", "Triangulating", "Excavating",
    "Scrutinizing", "Untangling", "Harvesting", "Marshalling", "Decoding",
    "Spelunking", "Percolating", "Divining", "Combing", "Tracking",
    "Unearthing", "Wrangling",
]

DEFAULT_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

ELLIPSIS = "…"


def pick_research_verb(seed: int | None = None) -> str:
    """Pick a fun research verb. A seed makes the choice deterministic for tests."""
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(RESEARCH_VERBS)


def theme_spinner_frames(theme: str = "bloomberg") -> list[str]:
    frames = theme_config(theme).get("spinner")
    return list(frames) if frames else list(DEFAULT_SPINNER)


def spinner_frame(theme: str, tick: int) -> str:
    frames = theme_spinner_frames(theme)
    return frames[tick % len(frames)]


def spinner_trail(tick: int, width: int = 7) -> str:
    """A single dot bouncing left↔right across `width` slots (search beam)."""
    width = max(1, width)
    if width == 1:
        return "●"
    span = 2 * (width - 1)
    pos = tick % span
    if pos >= width:
        pos = span - pos
    return "".join("●" if i == pos else "·" for i in range(width))


def spinner_progress_bar(tick: int, width: int = 40) -> str:
    """A lit segment sweeping across a track, à la a scanning progress bar."""
    width = max(4, width)
    seg = max(3, width // 4)
    span = width + seg
    start = tick % span - seg
    cells = ["█" if start <= i < start + seg else "░" for i in range(width)]
    return "[" + "".join(cells) + "]"


_QUERY_STOPWORDS = {"the", "and", "for", "with", "from", "into", "over", "study",
                    "studies", "research", "latest", "recent", "days", "last", "or"}


def rotating_query_term(query: str, tick: int, period: int = 6) -> str:
    """One keyword of the query, cycling every ``period`` ticks.

    Gives the waiting user something to read while a search runs: the displayed
    focus word advances through the query's significant terms instead of sitting
    static. Returns "" for an empty query.
    """
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", str(query or ""))
             if w.lower() not in _QUERY_STOPWORDS]
    if not words:
        return ""
    return words[(tick // max(1, period)) % len(words)]


def format_search_animation(verb: str, query: str = "", theme: str = "bloomberg", tick: int = 0, elapsed: float = 0.0) -> str:
    """One-line animated search status, e.g. '✳ ⠹ Investigating "minoxidil"…  ·●·  ⌕ safety  (3s · searching)'."""
    cfg = theme_config(theme)
    sparkle = cfg.get("sparkle", "✳")
    glyph = spinner_frame(theme, tick)
    trail = spinner_trail(tick)
    q = " ".join(str(query or "").split())
    snippet = f' "{q[:46]}"' if q else ""
    secs = max(0, int(elapsed))
    focus = rotating_query_term(q, tick)
    focus_part = f"  ⌕ {focus}" if focus else ""
    return f"{sparkle} {glyph} {verb}{snippet}{ELLIPSIS}  {trail}{focus_part}  ({secs}s · searching)"


# Ordered checklist of deep-research stages, mirrored by the progress_cb keys
# reported from deep_research()/search_and_store_from_tui(). The panel shows each
# stage with a ✓ (done), a live spinner (active), or a dim · (pending) — the
# "ultracode" running-checklist feel.
PROGRESS_STAGES: list[tuple[str, str]] = [
    ("sources", "Search the literature"),
    ("dedup", "Deduplicate hits"),
    ("process", "Read & summarize PDFs"),
    ("evaluate", "Score & evaluate"),
]

# Meta-analysis runs through its own (longer) phases; reported by
# run_meta_analysis_from_tui so the same progress band/panel animates for /meta.
META_STAGES: list[tuple[str, str]] = [
    ("collect", "Collect candidate studies"),
    ("extract", "Read & extract data"),
    ("synthesize", "Synthesize the evidence"),
    ("score", "Score & store"),
]


# Height (incl. top/bottom border) of the bottom search-progress band.
PROGRESS_BAND_H = 6
# Minimum rows the article/watchlist boxes need to stay usable.
MIN_WORK_H = 10
# Lines paged per PgDn/PgUp/Space when reading a long document (meta-analysis
# narrative, or a study's full detail/summary in reading mode).
PAGE_LINES = 15


def progress_band_height(h: int, top: int, searching: bool) -> int:
    """Rows to reserve for the bottom search band — 0 when it shouldn't show.

    Returns PROGRESS_BAND_H only while a search is reporting AND the terminal is
    tall enough to keep >= MIN_WORK_H rows of boxes above the band (so work_h
    never clamps to its floor and lets the boxes run into the band). On a short
    terminal it returns 0 and the bottom spinner carries the progress instead.
    """
    if not searching:
        return 0
    return PROGRESS_BAND_H if (h - top - 4 - PROGRESS_BAND_H >= MIN_WORK_H) else 0


def format_progress_band(snap: dict, theme: str = "bloomberg", tick: int = 0,
                         elapsed: float = 0.0, width: int = 100,
                         stages: list[tuple[str, str]] | None = None) -> list[str]:
    """Compact, horizontal progress for the bottom band (≤4 lines).

    Unlike the tall vertical panel, this fits a thin bottom strip so the article
    boxes above stay full-size and navigable while a search runs. ``stages`` is
    the checklist to map keys→labels against (search by default, META_STAGES for
    a meta run). Pure + deterministic for unit testing.
    """
    stages = stages or PROGRESS_STAGES
    cfg = theme_config(theme)
    sparkle = cfg.get("sparkle", "✳")
    glyph = spinner_frame(theme, tick)
    active = snap.get("stage") or ""
    done = set(snap.get("history") or [])
    cur, tot = int(snap.get("current") or 0), int(snap.get("total") or 0)
    secs = max(0, int(elapsed))
    chips = []
    for key, label in stages:
        short = label.split()[0]  # Search / Deduplicate / Read / Score
        if key in done:
            chips.append(f"✓ {short}")
        elif key == active:
            ctr = f" {cur}/{tot}" if tot else ""
            chips.append(f"{glyph} {short}{ctr}")
        else:
            chips.append(f"· {short}")
    line1 = f"{sparkle} {secs}s   " + "  ·  ".join(chips)
    line2 = spinner_progress_bar(tick, min(max(12, width - 2), 70))
    results = snap.get("results") or []
    fparts = []
    for r in results[-3:]:
        mark = {"hot": "★", "solid": "•", "weak": "⚠"}[score_band(r.get("label", ""), int(r.get("score") or 0))]
        fparts.append(f"{mark}{int(r.get('score') or 0)} {str(r.get('title', '')).strip()[:24]}")
    line3 = ("found  " + "   ".join(fparts)) if fparts else "scanning sources…"
    reaction = " ".join(str(snap.get("reaction") or "").split())
    lines = [line1[:width], line2, line3[:width]]
    if reaction:
        lines.append(reaction[:width])
    return lines[:4]


def format_progress_panel(snap: dict, theme: str = "bloomberg", tick: int = 0,
                          elapsed: float = 0.0, width: int = 60,
                          stages: list[tuple[str, str]] | None = None) -> list[str]:
    """Render the staged progress panel as plain lines (no curses).

    ``snap`` is a :meth:`ProgressChannel.snapshot`; ``stages`` is the checklist
    (search by default, META_STAGES for a meta run). Pure + deterministic so it
    is unit-testable: same inputs → same lines.
    """
    stages = stages or PROGRESS_STAGES
    cfg = theme_config(theme)
    sparkle = cfg.get("sparkle", "✳")
    glyph = spinner_frame(theme, tick)
    active = snap.get("stage") or ""
    done = set(snap.get("history") or [])
    current = int(snap.get("current") or 0)
    total = int(snap.get("total") or 0)
    detail = " ".join(str(snap.get("detail") or "").split())
    secs = max(0, int(elapsed))

    lines = [f"{sparkle} Researching  ·  {secs}s elapsed"]
    for key, label in stages:
        if key in done:
            marker, text = "✓", label
        elif key == active:
            counter = f"  {current}/{total}" if total else ""
            extra = f"  — {detail}" if detail else ""
            marker, text = glyph, f"{label}{counter}{extra}"
        else:
            marker, text = "·", label
        row = f"  {marker} {text}"
        lines.append(row[: max(8, width)])
    # A sweeping scan bar under the checklist ties the panel together visually.
    lines.append("  " + spinner_progress_bar(tick, min(40, max(12, width - 4))))

    # Stream findings in as they're scored, newest last, with a score badge.
    results = snap.get("results") or []
    if results:
        lines.append(f"  ── findings ({len(results)}) ───────────")
        band_mark = {"hot": "★", "solid": "•", "weak": "⚠"}
        for r in results[-5:]:
            mark = band_mark[score_band(r.get("label", ""), int(r.get("score") or 0))]
            row = f"  {mark} {int(r.get('score') or 0):>3}  {r.get('title', '')}"
            lines.append(row[: max(8, width)])
    # The companion's running remark on the latest find.
    reaction = " ".join(str(snap.get("reaction") or "").split())
    if reaction:
        lines.append("  " + reaction[: max(8, width - 2)])
    return lines


def search_progress_snapshot(state: "TuiState") -> dict | None:
    """The progress snapshot to render in the detail pane, or None.

    Returns None unless a background task is actively REPORTING progress (it has
    a current stage or some history). A meta-analysis run attaches a
    ProgressChannel it never writes to, so this keeps its detail pane as normal
    content + the bottom spinner instead of a frozen, mislabeled search checklist.
    """
    task = getattr(state, "task", None)
    if not state.busy or task is None or task.progress is None:
        return None
    snap = task.progress.snapshot()
    return snap if (snap.get("stage") or snap.get("history")) else None


def render_spin_demo(theme: str = "bloomberg", frames: int = 14, verb: str | None = None, query: str = "oral minoxidil safety") -> str:
    """Non-interactive dump of consecutive spinner frames for eyeballing / CI."""
    verb = verb or RESEARCH_VERBS[0]
    cfg = theme_config(theme)
    header = f"Spinner demo — theme:{cfg['name']} ({cfg['accent_name']}) · verb:{verb}"
    lines = [header, "-" * len(header)]
    for tick in range(max(1, frames)):
        lines.append(format_search_animation(verb, query, theme, tick, elapsed=tick * 0.8))
        lines.append("    " + spinner_progress_bar(tick, 40))
    return "\n".join(lines)


def render_progress_demo(theme: str = "bloomberg") -> str:
    """Non-interactive dump of the staged 'ultracode' progress panel advancing.

    Replays a scripted deep-research run through a real :class:`ProgressChannel`
    so --progress-demo shows exactly what the UI paints, frame by frame.
    """
    cfg = theme_config(theme)
    header = f"Progress demo — theme:{cfg['name']} ({cfg['accent_name']})"
    out = [header, "-" * len(header)]
    chan = ProgressChannel()
    # (tick, elapsed, stage, detail, current, total) — a representative run.
    script = [
        ("sources", "PubMed", 0, 0), ("sources", "CrossRef", 0, 0),
        ("sources", "OpenAlex", 0, 0), ("dedup", "5 unique", 5, 5),
        ("process", "PDF · oral minoxidil RCT", 1, 5),
        ("process", "AI synthesis · oral minoxidil RCT", 1, 5),
        ("process", "PDF · finasteride cohort", 3, 5),
        ("evaluate", "scoring & storing", 5, 5),
    ]
    # Findings stream in as the process stage advances, each with the companion's
    # in-character reaction — mirroring what a live search paints.
    streamed = {
        6: ("Oral minoxidil for hair loss: a randomized trial", 84, "HOT"),
        7: ("Finasteride safety: retrospective cohort", 58, "NEW"),
        8: ("Single-case minoxidil report (preprint)", 22, "RISK"),
    }
    for tick, (stage, detail, cur, tot) in enumerate(script):
        chan.report(stage, detail, cur, tot)
        if tick in streamed:
            title, sc, lbl = streamed[tick]
            chan.add_result(title, sc, lbl, companion_reaction(theme, lbl, sc))
        out.append(f"--- frame {tick}  ({stage}) ---")
        out.extend(format_progress_panel(chan.snapshot(), theme, tick,
                                         elapsed=tick * 1.5, width=58))
    return "\n".join(out)


# ── Search configuration screen ────────────────────────────────────────────
# Sensibilité = recall/breadth → which sources are queried.
SENSITIVITY_LEVELS = [
    ("Pointue", ["pubmed"]),
    ("Précise", ["pubmed", "crossref"]),
    ("Équilibrée", list(DEFAULT_SOURCES)),       # the canonical default search
    ("Large", ["pubmed", "crossref", "openalex", "europe_pmc"]),
    ("Exhaustive", list(DEFAULT_DEEP_SOURCES)),   # matches the deep pipeline's net
]
# Profondeur = per-article analysis depth → toggles the deep (full-text + AI) path.
DEPTH_LEVELS = [
    ("Survol", False, "rapide · métadonnées + abstracts"),
    ("Approfondie", True, "full-text + synthèse IA par article"),
    ("Exhaustive", True, "full-text + synthèse IA · filet élargi"),
]
# Output language — governs studies, summaries and meta-analyses alike (the one
# `lang` value threaded through search, deep-research and meta-analysis calls).
LANG_LEVELS = [
    ("Français", "fr", "studies, summaries & meta-analyses generated in French"),
    ("English", "en", "studies, summaries & meta-analyses generated in English"),
]
DEFAULT_LANG_IDX = next((i for i, lv in enumerate(LANG_LEVELS) if lv[1] == DEFAULT_LANG), 0)
MAX_MIN, MAX_MAX = 1, 30
CONFIG_GAUGE_W = 30
CONFIG_FOCUS_COUNT = 4

# Meta-analysis config: which databases to search and how many studies to pull.
# Only the four sources meta_analysis.py actually implements are offered here.
META_SOURCES_LEVELS = [
    ("Essential", ["pubmed", "openalex"]),
    ("Standard", ["pubmed", "crossref", "openalex"]),
    ("Extended", ["pubmed", "crossref", "openalex", "europe_pmc"]),
]
META_MAX_MIN, META_MAX_MAX = 3, 20
META_DEPTH_LEVELS = [
    ("Low",    "brief · abstract summaries via MiniMax"),
    ("Medium", "synthesis · numbers + study limitations"),
    ("Deep",   "exhaustive · all PDFs downloaded + per-study AI enrichment"),
]
META_CONFIG_FOCUS_COUNT = 3


def resolve_search_config(state: "TuiState") -> dict[str, Any]:
    """Turn the four configured knobs into concrete run_search parameters."""
    _, sources = SENSITIVITY_LEVELS[clamp_index(state.sensitivity_idx, len(SENSITIVITY_LEVELS))]
    depth_label, deep, depth_note = DEPTH_LEVELS[clamp_index(state.depth_idx, len(DEPTH_LEVELS))]
    lang_label, lang, lang_note = LANG_LEVELS[clamp_index(state.lang_idx, len(LANG_LEVELS))]
    eff_max = max(MAX_MIN, min(MAX_MAX, int(state.search_max)))
    if state.depth_idx >= len(DEPTH_LEVELS) - 1:  # Exhaustive widens the net
        eff_max = min(MAX_MAX, int(round(eff_max * 1.5)))
    return {
        "max": eff_max,
        "sources": list(sources),
        "deep": deep,
        # Sci-Hub is an opt-in PDF fallback, independent of depth: the depth knob
        # only governs the per-article AI analysis (MiniMax summaries +
        # appraisal). Off unless an operator set DR_NEWPAPER_ALLOW_SCIHUB=1.
        "allow_scihub": scihub_enabled(),
        "sensitivity": SENSITIVITY_LEVELS[clamp_index(state.sensitivity_idx, len(SENSITIVITY_LEVELS))][0],
        "depth": depth_label,
        "depth_note": depth_note,
        # The single language value shared by search, deep-research and
        # meta-analysis — governs the language studies/summaries/meta-analyses
        # are generated in (see LANG_LEVELS).
        "lang": lang,
        "lang_label": lang_label,
        "lang_note": lang_note,
    }


def resolve_meta_config(state: "TuiState") -> dict[str, Any]:
    """Turn the meta-analysis config knobs into concrete run parameters."""
    _, sources = META_SOURCES_LEVELS[clamp_index(state.meta_sources_idx, len(META_SOURCES_LEVELS))]
    depth_label, depth_note = META_DEPTH_LEVELS[clamp_index(state.meta_depth_idx, len(META_DEPTH_LEVELS))]
    return {
        "max": max(META_MAX_MIN, min(META_MAX_MAX, int(state.meta_max_articles))),
        "sources": list(sources),
        "analysis_depth": depth_label.lower(),
        "full_text": depth_label == "Deep",
        "depth_note": depth_note,
    }


def _config_gauge(frac: float, width: int, tick: int, focused: bool) -> str:
    """A filled gauge with a shimmer cell sweeping the filled region when focused."""
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    cells = []
    for i in range(width):
        if i < filled:
            cells.append("▒" if (focused and filled and i == tick % filled) else "█")
        else:
            cells.append("░")
    return "▕" + "".join(cells) + "▏"


# Every knob renders its bar as: GUTTER + gauge + VALUE_GAP + value-chip, so the
# gauge and the value column line up vertically across all three knobs. (Before,
# the max slider prefixed the gauge with "lo " which shoved it 3 cols right of the
# segmented gauges below it — the misalignment.)
_CONFIG_GUTTER = "     "   # 5 spaces — the shared left column for every bar row
_CONFIG_VALUE_GAP = "  "   # between the gauge's right cap and the value chip


def _config_value_slider(value: int, lo: int, hi: int, width: int, tick: int, focused: bool) -> str:
    frac = (value - lo) / (hi - lo) if hi > lo else 0.0
    return f"{_config_gauge(frac, width, tick, focused)}{_CONFIG_VALUE_GAP}⟨ {value:>2} / {hi} ⟩"


def _config_segmented(levels: list, idx: int, width: int, tick: int, focused: bool) -> tuple[str, str]:
    chips = [f"[ {lv[0]} ]" if i == idx else f" {lv[0]} " for i, lv in enumerate(levels)]
    frac = idx / (len(levels) - 1) if len(levels) > 1 else 0.0
    bar = f"{_config_gauge(frac, width, tick, focused)}{_CONFIG_VALUE_GAP}⟨ {levels[idx][0]} ⟩"
    return "·".join(chips), bar


def _config_caret(tick: int, focused: bool) -> str:
    if not focused:
        return "  "
    return "▸ " if (tick // 4) % 2 == 0 else "▷ "


# A celebratory shock-wave when the user pushes analysis depth to its deepest
# rung (the same moment full-text + AI synthesis + Sci-Hub fallback all arm). It
# ignites at the depth cursor — parked at the right end of the gauge once you slam
# it to max — and a tiered, theme-coloured front sweeps outward until it washes
# every interior border of the config box: an explosion contained in the tab.
_EXPLOSION_SPEED = 3    # cells the shock front advances per animation frame (lower = more visible sweep)
_EXPLOSION_BAND = 12    # depth, in cells, of the colour band trailing the front (higher = thicker blast)
_EXPLOSION_TIERS = 6    # colour/intensity tiers across the band (0 = bright crest)


def _explosion_origin(width: int, height: int, origin: tuple[int, int] | None) -> tuple[int, int]:
    """Clamp the requested ignition point into the grid; default to its centre."""
    oy, ox = origin if origin is not None else (height // 2, width // 2)
    return max(0, min(height - 1, oy)), max(0, min(width - 1, ox))


def explosion_span(width: int, height: int, origin: tuple[int, int] | None = None) -> int:
    """Frames the burst lasts: enough for the trailing band to clear the far corner.

    Because the ignition point is off-centre (the cursor at max), the farthest
    corner — not ``width//2`` — sets the duration, so the wave reaches every edge.
    """
    if width < 1 or height < 1:
        return 0
    oy, ox = _explosion_origin(width, height, origin)
    far = max(oy + ox, oy + (width - 1 - ox),
              (height - 1 - oy) + ox, (height - 1 - oy) + (width - 1 - ox))
    return (far + _EXPLOSION_BAND) // _EXPLOSION_SPEED + 1


def explosion_cells(frame: int, width: int, height: int, theme: str = "bloomberg",
                    origin: tuple[int, int] | None = None,
                    core: "str | None" = None,
                    ring: "str | None" = None) -> list[list[tuple[str, int] | None]]:
    """A pure, deterministic expanding shock-wave as a grid of ``(glyph, tier)`` cells.

    ``tier`` 0 is the bright leading crest at the shock front; higher tiers fade
    behind it toward the ignition point. Cells the front has not yet reached, or
    that the band has already swept past, are ``None`` so the config text shows
    through behind the wave. Returns ``[]`` for a spent ``frame`` or a degenerate
    box. Fronts use integer Manhattan distance (diamonds) — no ``math`` needed —
    and every glyph is a single cell.

    ``core`` and ``ring`` override the theme's sparkle/glyph glyphs when supplied.
    """
    if width < 1 or height < 1 or frame < 0 or frame >= explosion_span(width, height, origin):
        return []
    cfg = theme_config(theme)
    core = core if core is not None else cfg.get("sparkle", "✳")
    ring = ring if ring is not None else cfg.get("glyph", "◆")
    oy, ox = _explosion_origin(width, height, origin)
    front = frame * _EXPLOSION_SPEED
    grid: list[list[tuple[str, int] | None]] = []
    for r in range(height):
        row: list[tuple[str, int] | None] = []
        for c in range(width):
            depth = front - (abs(c - ox) + abs(r - oy))   # 0 at the front, grows inward
            if 0 <= depth <= _EXPLOSION_BAND:
                tier = min(_EXPLOSION_TIERS - 1, depth * _EXPLOSION_TIERS // (_EXPLOSION_BAND + 1))
                glyph = core if tier == 0 else ring if tier <= _EXPLOSION_TIERS // 2 else "·"
                row.append((glyph, tier))
            else:
                row.append(None)
        grid.append(row)
    return grid


def explosion_frames(frame: int, width: int, height: int, theme: str = "bloomberg",
                     origin: tuple[int, int] | None = None,
                     core: "str | None" = None,
                     ring: "str | None" = None) -> list[str]:
    """Glyph-only view of :func:`explosion_cells` (spaces for empty cells)."""
    return ["".join(cell[0] if cell else " " for cell in row)
            for row in explosion_cells(frame, width, height, theme, origin, core, ring)]


# Per-tier (colour-pair index, extra attr) for the shock-wave, brightest crest
# first (one entry per _EXPLOSION_TIERS). Spanning the theme's own pairs (title →
# primary → status → secondary → muted) gives a palette gradient on multi-hue
# themes; the BOLD→DIM ramp keeps the depth readable on single-hue themes (e.g.
# matrix, where every pair is green).
_EXPLOSION_TIER_ATTR = [
    (3, curses.A_BOLD),
    (2, curses.A_BOLD),
    (2, curses.A_BOLD),
    (4, curses.A_BOLD),
    (1, 0),
    (6, curses.A_DIM),
]

# Meta-analysis deep-mode explosion: vivid fire palette using danger/red (pair 5)
# for the inner rings, keeping A_BOLD through tier 3 for a more nuclear feel.
_META_EXPLOSION_TIER_ATTR = [
    (3, curses.A_BOLD),   # ★ core — title color, bold
    (5, curses.A_BOLD),   # fire ring — danger/red, bold
    (5, curses.A_BOLD),   # inner fire — danger/red, bold
    (2, curses.A_BOLD),   # hot glow — primary, bold
    (3, 0),               # warm fade — title, normal
    (6, curses.A_DIM),    # ember tail — muted, dim
]

# Where the burst grid sits relative to the config box, and where the depth cursor
# parks when slammed to max. Deriving the origin's column and the paint offset from
# the same two pads keeps the ignition point glued to the gauge's right end.
_CONFIG_BURST_PAD = 1   # cells the burst grid sits inside the config box border
_CONFIG_TEXT_PAD = 4    # screen column (box_x + this) where each config row starts


def _config_burst_origin(rows: list[tuple[str, str, bool]]) -> tuple[int, int]:
    """Ignition point (grid y, x) at the right end of the depth gauge's fill.

    The depth gauge is the last ``gauge`` row; at max its fill runs the whole
    ``CONFIG_GAUGE_W``, so the cursor sits at its right end. Coordinates are in the
    burst grid, which is offset ``_CONFIG_BURST_PAD`` inside the box border.
    """
    gauge_idx = max((i for i, (_t, kind, _f) in enumerate(rows) if kind == "gauge"),
                    default=len(rows) // 2)
    gy = gauge_idx + 3 - _CONFIG_BURST_PAD                       # rows draw at box_y + 3 + idx
    gx = len(_CONFIG_GUTTER) + CONFIG_GAUGE_W + (_CONFIG_TEXT_PAD - _CONFIG_BURST_PAD)
    return gy, gx


def _config_lines(state: "TuiState", tick: int = 0) -> list[tuple[str, str, bool]]:
    """Rows of the config screen as (text, kind, focused) for both demo and curses."""
    focus = state.config_focus
    cfg = resolve_search_config(state)
    sens_label, sources = SENSITIVITY_LEVELS[clamp_index(state.sensitivity_idx, len(SENSITIVITY_LEVELS))]
    g = _CONFIG_GUTTER
    note_indent = g + "  → "  # notes sit one step inside the shared bar gutter
    rows: list[tuple[str, str, bool]] = []
    rows.append((f"{_config_caret(tick, focus == 0)}Nombre maximum d'études", "label", focus == 0))
    rows.append((f"{g}{_config_value_slider(int(state.search_max), MAX_MIN, MAX_MAX, CONFIG_GAUGE_W, tick, focus == 0)}", "gauge", focus == 0))
    rows.append(("", "blank", False))
    rows.append((f"{_config_caret(tick, focus == 1)}Sensibilité de la recherche  ·  recall", "label", focus == 1))
    seg_lab, seg_bar = _config_segmented(SENSITIVITY_LEVELS, clamp_index(state.sensitivity_idx, len(SENSITIVITY_LEVELS)), CONFIG_GAUGE_W, tick, focus == 1)
    rows.append((f"{g}{seg_lab}", "seg", focus == 1))
    rows.append((f"{g}{seg_bar}", "gauge", focus == 1))
    rows.append((f"{note_indent}sources : {', '.join(sources)}", "note", False))
    rows.append(("", "blank", False))
    rows.append((f"{_config_caret(tick, focus == 2)}Profondeur d'analyse", "label", focus == 2))
    seg_lab, seg_bar = _config_segmented(DEPTH_LEVELS, clamp_index(state.depth_idx, len(DEPTH_LEVELS)), CONFIG_GAUGE_W, tick, focus == 2)
    rows.append((f"{g}{seg_lab}", "seg", focus == 2))
    rows.append((f"{g}{seg_bar}", "gauge", focus == 2))
    rows.append((f"{note_indent}{cfg['depth_note']}", "note", False))
    rows.append(("", "blank", False))
    rows.append((f"{_config_caret(tick, focus == 3)}Output language  ·  studies, summaries & meta-analyses", "label", focus == 3))
    seg_lab, seg_bar = _config_segmented(LANG_LEVELS, clamp_index(state.lang_idx, len(LANG_LEVELS)), CONFIG_GAUGE_W, tick, focus == 3)
    rows.append((f"{g}{seg_lab}", "seg", focus == 3))
    # "lang_gauge", not "gauge": keeps this row out of _config_burst_origin's
    # scan, so the depth explosion still ignites at the depth gauge (the
    # animation's documented anchor), not wherever this knob happens to sit.
    rows.append((f"{g}{seg_bar}", "lang_gauge", focus == 3))
    rows.append((f"{note_indent}{cfg['lang_note']}", "note", False))
    rows.append(("", "blank", False))
    rows.append((f"   Aperçu :  ≈{cfg['max']} études  ·  {len(sources)} source(s)  ·  synthèse IA : {'oui' if cfg['deep'] else 'non'}", "preview", False))
    rows.append(("", "blank", False))
    rows.append(("   ↑/↓ choisir     ←/→ ajuster     Enter valider     Esc annuler", "help", False))
    return rows


def _meta_config_lines(state: "TuiState", tick: int = 0) -> list[tuple[str, str, bool]]:
    """Rows for the meta-analysis config tab (studies count + databases + analysis depth)."""
    focus = state.config_focus
    mc = resolve_meta_config(state)
    g = _CONFIG_GUTTER
    note_indent = g + "  → "
    rows: list[tuple[str, str, bool]] = []
    rows.append((f"{_config_caret(tick, focus == 0)}Number of studies", "label", focus == 0))
    rows.append((f"{g}{_config_value_slider(int(state.meta_max_articles), META_MAX_MIN, META_MAX_MAX, CONFIG_GAUGE_W, tick, focus == 0)}", "gauge", focus == 0))
    rows.append(("", "blank", False))
    rows.append((f"{_config_caret(tick, focus == 1)}Reference databases", "label", focus == 1))
    seg_lab, seg_bar = _config_segmented(META_SOURCES_LEVELS, clamp_index(state.meta_sources_idx, len(META_SOURCES_LEVELS)), CONFIG_GAUGE_W, tick, focus == 1)
    rows.append((f"{g}{seg_lab}", "seg", focus == 1))
    rows.append((f"{g}{seg_bar}", "gauge", focus == 1))
    rows.append((f"{note_indent}sources: {', '.join(mc['sources'])}", "note", False))
    rows.append(("", "blank", False))
    rows.append((f"{_config_caret(tick, focus == 2)}Analysis depth", "label", focus == 2))
    seg_lab2, seg_bar2 = _config_segmented(META_DEPTH_LEVELS, clamp_index(state.meta_depth_idx, len(META_DEPTH_LEVELS)), CONFIG_GAUGE_W, tick, focus == 2)
    rows.append((f"{g}{seg_lab2}", "seg", focus == 2))
    rows.append((f"{g}{seg_bar2}", "gauge", focus == 2))
    rows.append((f"{note_indent}{mc['depth_note']}", "note", False))
    rows.append(("", "blank", False))
    # Language is a single knob shared with the Search tab (see LANG_LEVELS) —
    # surfaced here read-only so it stays discoverable from this tab too.
    lang_label = resolve_search_config(state)["lang_label"]
    rows.append((f"   Preview: ≈{mc['max']} studies · {len(mc['sources'])} source(s) · depth: {mc['analysis_depth']} · lang: {lang_label} (Search tab)", "preview", False))
    rows.append(("", "blank", False))
    rows.append(("   ↑/↓ choose   ←/→ adjust   Tab search config   Enter confirm   Esc cancel", "help", False))
    return rows


def render_config_screen(state: "TuiState", tick: int = 0) -> list[str]:
    """Plain-text config screen for --config-demo and tests."""
    tab_line = ("[ Search ]  ·  Meta-Analysis" if state.config_tab == "search"
                else "  Search  ·  [ Meta-Analysis ]")
    rows = (_config_lines if state.config_tab == "search" else _meta_config_lines)(state, tick)
    return [tab_line, ""] + [text for text, _kind, _focus in rows]


def render_explosion_demo(theme: str = "bloomberg", width: int = 60, height: int = 15) -> str:
    """Non-interactive dump of the deepest-depth explosion, frame by frame.

    Ignites at a cursor-like origin (the right end of the gauge) so the dump
    exercises the real off-centre propagation, not a centred special case.
    """
    cfg = theme_config(theme)
    origin = (height // 2, min(width - 1, len(_CONFIG_GUTTER) + CONFIG_GAUGE_W + 3))
    header = f"Explosion demo — theme:{cfg['name']} ({cfg['accent_name']}) · origin={origin}"
    out = [header, "-" * len(header)]
    for frame in range(explosion_span(width, height, origin)):
        out.append(f"--- frame {frame} ---")
        out.extend(explosion_frames(frame, width, height, theme, origin))
    return "\n".join(out)


def config_adjust(state: "TuiState", delta: int) -> None:
    if state.config_tab == "meta":
        if state.config_focus == 0:
            state.meta_max_articles = max(META_MAX_MIN, min(META_MAX_MAX, int(state.meta_max_articles) + delta))
        elif state.config_focus == 1:
            state.meta_sources_idx = max(0, min(len(META_SOURCES_LEVELS) - 1, state.meta_sources_idx + delta))
        elif state.config_focus == 2:
            state.meta_depth_idx = max(0, min(len(META_DEPTH_LEVELS) - 1, state.meta_depth_idx + delta))
    else:
        if state.config_focus == 0:
            state.search_max = max(MAX_MIN, min(MAX_MAX, int(state.search_max) + delta))
        elif state.config_focus == 1:
            state.sensitivity_idx = max(0, min(len(SENSITIVITY_LEVELS) - 1, state.sensitivity_idx + delta))
        elif state.config_focus == 2:
            state.depth_idx = max(0, min(len(DEPTH_LEVELS) - 1, state.depth_idx + delta))
        elif state.config_focus == 3:
            state.lang_idx = max(0, min(len(LANG_LEVELS) - 1, state.lang_idx + delta))


def config_handle_key(state: "TuiState", key: int) -> bool:
    """Apply a key to the config screen. Returns True if the screen should close."""
    if key in (27, ord("o"), ord("O"), 10, 13, curses.KEY_ENTER):
        return True
    if key == ord("\t"):  # Tab switches between Search and Meta config
        state.config_tab = "meta" if state.config_tab == "search" else "search"
        state.config_focus = 0  # reset focus when switching tabs
        return False
    focus_count = META_CONFIG_FOCUS_COUNT if state.config_tab == "meta" else CONFIG_FOCUS_COUNT
    if key in (curses.KEY_UP, ord("k"), ord("K")):
        state.config_focus = (state.config_focus - 1) % focus_count
    elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
        state.config_focus = (state.config_focus + 1) % focus_count
    elif key in (curses.KEY_LEFT, ord("h"), ord("H")):
        config_adjust(state, -1)
    elif key in (curses.KEY_RIGHT, ord("l"), ord("L")):
        config_adjust(state, +1)
    return False


def render_config_demo(theme: str = "bloomberg", frames: int = 8) -> str:
    """Non-interactive dump of consecutive config-screen frames."""
    state = TuiState(db_path=Path("."), articles=[], watchlists=[], theme=theme, config_focus=1)
    out = [f"Config demo — theme:{theme_config(theme)['name']}"]
    for tick in range(max(1, frames)):
        out.append(f"--- frame {tick} ---")
        out.extend(render_config_screen(state, tick))
    return "\n".join(out)


def _fit(line: str, width: int) -> str:
    return line[: max(0, width)]


def _char_cells(ch: str) -> int:
    """Display columns for a char: wide/fullwidth (e.g. 📄, CJK) = 2, else 1."""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _disp_width(s: str) -> int:
    return sum(_char_cells(ch) for ch in s)


def _fit_display(s: str, width: int) -> str:
    """Truncate to at most `width` display columns (not code points)."""
    out, used = [], 0
    for ch in s:
        cw = _char_cells(ch)
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out)


def _frame_line(content: str, inner: int) -> str:
    """Wrap `content` in │…│, padding by display width so the right border lines up
    even when the content holds wide glyphs (📄) that span two terminal cells."""
    body = _fit_display(content, inner)
    return "│" + body + " " * max(0, inner - _disp_width(body)) + "│"


# DR · NP wordmark, composed from two halves with a dot block between them so the
# "point entre Dr et NP" sits exactly in the gap and stays aligned at any width.
_LOGO_FIELD = 43  # width reserved for the wordmark column (mascot starts after it)
_MASCOT_X = 1 + 2 + _LOGO_FIELD + 2  # screen column where the companion art begins


def _logo_rows(theme: str = "bloomberg") -> list[str]:
    """Render the DR · NP wordmark in the theme's font and fill character."""
    cfg = theme_config(theme)
    font = LOGO_FONTS.get(cfg.get("logo_font", "ansishadow"), LOGO_FONTS["ansishadow"])
    fill = cfg.get("logo_fill", "█")
    dr, np_, dot = list(font["dr"]), list(font["np"]), list(font["dot"])
    n = max(len(dr), len(np_), len(dot))
    dr += [""] * (n - len(dr)); np_ += [""] * (n - len(np_)); dot += [""] * (n - len(dot))
    dr_w = max(len(r) for r in font["dr"])
    rows = [f"{dr[i]:<{dr_w}} {dot[i]:<2} {np_[i]}" for i in range(n)]
    return [r.replace("█", fill) for r in rows] if fill != "█" else rows


def _front_spec(theme: str = "bloomberg", width: int = 120) -> list[tuple[str, str, tuple[int, str] | None]]:
    """Build the front/splash block as (line, color_role, emblem_overlay) rows.

    The frame charset, wordmark font and companion all come from the theme, so
    each art direction reshapes the whole front. emblem_overlay is
    (x, text) for the companion art so the header can paint it in its own colour.
    """
    cfg = theme_config(theme)
    width = max(72, width)
    inner = max(1, width - 2)
    bs = border_style(cfg.get("border"))
    mascot = cfg.get("mascot", []) or []

    def wrap(content: str, role: str) -> tuple[str, str, tuple[int, str] | None]:
        body = _fit_display(content, inner)
        return (bs["v"] + body + " " * max(0, inner - _disp_width(body)) + bs["v"], role, None)

    logo = _logo_rows(theme)
    n = max(len(logo), len(mascot))
    pad_top = (n - len(logo)) // 2
    logo = [""] * pad_top + logo + [""] * max(0, n - len(logo) - pad_top)
    mascot = list(mascot) + [""] * max(0, n - len(mascot))

    # Place the companion immediately to the right of the wordmark (left-aligned),
    # falling back leftwards only if the terminal is too narrow to keep it visible.
    comp_w = max((_disp_width(m) for m in mascot if m.strip()), default=0)
    logo_w = max((_disp_width(r) for r in logo if r), default=0)
    comp_x = min(2 + logo_w + 3, max(2, inner - comp_w)) if comp_w else inner
    rows: list[tuple[str, str, tuple[int, str] | None]] = [(bs["tl"] + bs["h"] * inner + bs["tr"], "border", None)]
    for i in range(n):
        left = _fit_display("  " + logo[i], max(0, comp_x - 1))  # truncate wordmark before the companion
        left += " " * max(0, comp_x - _disp_width(left))         # pad up to the companion column
        line, _r, _e = wrap(left + mascot[i], "brand")
        emblem = (1 + comp_x, mascot[i]) if mascot[i].strip() else None
        rows.append((line, "brand", emblem))
    rows.append((bs["ml"] + bs["h"] * inner + bs["mr"], "border", None))
    rows.append(wrap(f"  Dr · NewPaper {APP_VERSION}  ·  theme:{cfg['name']} ({cfg['accent_name']})  ·  companion:{cfg.get('mascot_name', '—')}", "meta"))
    tagline = cfg.get("tagline", "")
    if tagline:
        rows.append(wrap(f"  « {tagline} »", "tagline"))
    rows.append(wrap("  Controls: S Search · D Recent · M Meta · O Config · P PDF · A Eval · X Delete · Z Clear · C Theme · Tab · Q Quit", "controls"))
    texture = cfg.get("texture", "")
    if texture:
        rows.append(wrap(f"  {texture}", "texture"))
    rows.append((bs["bl"] + bs["h"] * inner + bs["br"], "border", None))
    return rows


def render_logo_block(theme: str = "bloomberg", width: int = 120) -> list[str]:
    """Compact Claude-style splash block for the top of the terminal."""
    return [line for line, _role, _emblem in _front_spec(theme, width)]


def render_tab_bar(active_tab: str = "current", focus: str = "articles", width: int = 80, theme: str = "bloomberg") -> str:
    """Plain text tab strip for the article modes."""
    cfg = theme_config(theme)
    active = (active_tab or "current").lower()
    tabs = [
        ("current", "Current"),
        ("saved", "Saved"),
        ("evaluation", "Evaluation"),
        ("watchlist", "Watchlist"),
        ("meta", "Meta"),
    ]
    chunks = []
    for key, label in tabs:
        if key == active:
            chunks.append(f" {cfg.get('glyph', '◆')} {label.upper()} ")
        else:
            chunks.append(f"   {label}   ")
    focus_label = "ARTICLES" if focus == "articles" else "WATCHLISTS"
    text = "│".join(chunks) + f"   Focus:{focus_label}"
    return _fit(text, max(1, width))


@dataclass
class TuiState:
    db_path: Path
    articles: list[dict[str, Any]]
    watchlists: list[dict[str, Any]]
    current_articles: list[dict[str, Any]] = field(default_factory=list)
    saved_articles: list[dict[str, Any]] = field(default_factory=list)
    watchlist_articles: list[dict[str, Any]] = field(default_factory=list)
    active_tab: str = "current"
    selected_article: int = 0
    selected_watchlist: int = 0
    focus: str = "articles"
    status: str = "Ready"
    last_query: str = ""
    theme: str = "bloomberg"
    mode: str = "normal"
    prompt_label: str = ""
    prompt_value: str = ""
    prompt_default: str = ""
    prompt_action: str = ""
    editing_watchlist_id: int = 0  # topic id mid-edit (rename / change subject)
    pending_topic_subject: str = ""  # subject captured while creating a topic (name comes after)
    # Event-driven companion pop-up (the transient bubble; see update_companion_popups).
    popup_text: str = ""
    popup_band: str = ""   # hot | solid | weak | info | error
    popup_born: float = 0.0
    popup_until: float = 0.0
    last_interaction: float = 0.0
    idle_popped: bool = False
    busy_nudge_i: int = 0   # count of recurring wait-nudges fired this busy session
    popup_selection_id: int = -1   # study id the last selection pop-up was for
    idle_quip_i: int = 0
    busy: bool = False
    spinner_verb: str = ""
    spinner_query: str = ""
    spinner_tick: int = 0
    spinner_elapsed: float = 0.0
    search_max: int = DEFAULT_MAX_RESULTS  # canonical default, shared with bot/CLI
    sensitivity_idx: int = 2  # Équilibrée → the canonical DEFAULT_SOURCES
    depth_idx: int = 0  # Survol (fast, deep=False) by default
    lang_idx: int = DEFAULT_LANG_IDX  # output language for studies/summaries/meta-analyses
    config_focus: int = 0
    config_tab: str = "search"       # "search" | "meta" — active config tab
    meta_max_articles: int = 8       # studies meta-analysis pulls + feeds to MiniMax
    meta_sources_idx: int = 2        # Extended (all 4 sources) by default
    meta_depth_idx: int = 1          # Medium by default (0=Low, 1=Medium, 2=Deep)
    # Non-blocking background work + companion notification (see BackgroundTask).
    task: "BackgroundTask | None" = None
    notif_text: str = ""
    notif_kind: str = ""  # "success" | "error" | "info" | "busy"
    notif_born: float = 0.0
    notif_until: float = 0.0
    anim_tick: int = 0  # free-running frame counter for toast/header animation
    # Config-screen "deepest depth" explosion: the config tick at which the burst
    # was armed. Large-negative sentinel = no burst pending (see run_config_screen).
    explosion_start_tick: int = -10**9
    # Meta-analysis reading tab: a list of past runs (left) + the open document
    # (right). meta_document/meta_query/meta_scroll hold the currently-open doc.
    meta_analyses: list[dict[str, Any]] = field(default_factory=list)
    selected_meta: int = 0
    meta_document: str = ""
    meta_query: str = ""
    meta_scroll: int = 0
    # Study reading mode: Enter on a study opens its full detail (deep summary +
    # abstract) for scrolling; ↑/↓ then scroll the pane instead of moving the
    # selection, and Esc/Enter leaves. detail_scroll is the line offset.
    reading: bool = False
    detail_scroll: int = 0


class ProgressChannel:
    """Thread-safe worker→UI progress relay for a background task.

    The worker (off the UI thread) calls :meth:`report` as it advances through
    pipeline stages; the UI thread reads a consistent :meth:`snapshot` each
    frame to paint the staged "ultracode"-style panel. Both ends are guarded by
    one lock, so this is the *one* object a worker may write to without breaking
    :class:`BackgroundTask`'s "never touch TuiState" invariant.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stage = ""
        self.detail = ""
        self.current = 0
        self.total = 0
        self.history: list[str] = []  # completed stage keys, in order
        self.results: list[dict[str, Any]] = []  # studies streamed in as scored
        self.reaction = ""  # latest in-character companion remark on a find

    def report(self, stage: str, detail: str = "", current: int = 0, total: int = 0) -> None:
        with self._lock:
            # When we move to a new stage, retire the previous one to history so
            # the panel can render it with a ✓.
            if stage != self.stage and self.stage and self.stage not in self.history:
                self.history.append(self.stage)
            self.stage = stage
            self.detail = detail
            self.current = current
            self.total = total

    def add_result(self, title: str, score: int = 0, label: str = "", comment: str = "") -> None:
        """Stream one freshly-scored study to the UI, with the companion's remark.

        Called from the worker as each result is stored/scored, so the user sees
        findings accrue at the bottom instead of waiting for the whole batch.
        """
        with self._lock:
            self.results.append({
                "title": " ".join(str(title or "").split())[:80],
                "score": int(score or 0),
                "label": str(label or ""),
            })
            if comment:
                self.reaction = comment

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "stage": self.stage,
                "detail": self.detail,
                "current": self.current,
                "total": self.total,
                "history": list(self.history),
                "results": list(self.results),
                "reaction": self.reaction,
            }


@dataclass
class BackgroundTask:
    """A unit of blocking work run off the UI thread.

    INVARIANT: ``worker`` must touch neither ``TuiState`` nor curses — it only
    does I/O and returns a plain result. ``apply`` runs back on the UI thread
    (via :func:`poll_background_task`) and is the *only* place that mutates state
    for this task; it returns the short toast text the companion will announce.
    The worker MAY write to ``progress`` (a :class:`ProgressChannel`) — the one
    sanctioned worker→UI side channel — to feed the staged progress panel.
    """
    kind: str
    verb: str
    query: str
    worker: "Callable[[], Any]"
    apply: "Callable[[TuiState, Any], str]"
    thread: "Any" = None
    result: Any = None
    error: "BaseException | None" = None
    done: bool = False
    started: float = 0.0
    progress: "ProgressChannel | None" = None


# How long a companion notification toast stays on screen after a task ends.
NOTIF_TTL = 6.0


def start_background_task(state: "TuiState", task: "BackgroundTask", now: float | None = None) -> "BackgroundTask":
    """Launch ``task.worker`` on a daemon thread and mark the UI busy.

    The worker runs entirely off the UI thread and must never touch ``state``
    (see :class:`BackgroundTask`). :func:`poll_background_task` later applies the
    result on the UI thread. Returns the task so callers can keep a handle.
    """
    now = time.monotonic() if now is None else now
    state.task = task
    state.busy = True
    state.spinner_verb = task.verb
    state.spinner_query = task.query
    state.spinner_tick = 0
    state.spinner_elapsed = 0.0
    # A fresh task supersedes any lingering toast from the previous one.
    state.notif_text = ""
    state.notif_until = 0.0
    task.started = now
    task.done = False
    task.error = None
    task.result = None

    def _runner() -> None:
        try:
            task.result = task.worker()
        except BaseException as exc:  # surfaced on the UI thread by poll_*
            task.error = exc
        finally:
            task.done = True

    task.thread = threading.Thread(target=_runner, daemon=True)
    task.thread.start()
    return task


def poll_background_task(state: "TuiState", now: float | None = None) -> bool:
    """Apply a finished background task on the UI thread; raise its companion toast.

    Returns ``True`` exactly once, on the call where the task completed, so the
    caller can resync the article view. Pure logic (no curses) so it is unit
    testable: build a :class:`BackgroundTask` with ``done=True`` and a result or
    error, then call this directly.
    """
    task = state.task
    if task is None:
        return False
    now = time.monotonic() if now is None else now
    if not task.done:
        if task.started:
            state.spinner_elapsed = now - task.started
        return False
    state.task = None
    state.busy = False
    state.spinner_elapsed = (now - task.started) if task.started else 0.0
    if task.error is not None:
        state.notif_kind = "error"
        text = f"{task.verb} failed: {task.error}"
    else:
        try:
            text = task.apply(state, task.result) or f"{task.verb} complete"
            state.notif_kind = "success"
        except BaseException as exc:  # an apply() bug must not crash the UI loop
            state.notif_kind = "error"
            text = f"{task.verb} failed: {exc}"
    state.notif_text = text
    state.notif_born = now
    state.notif_until = now + NOTIF_TTL
    # A finished search also raises a prominent pop-up (≤20s), interrupting any
    # selection/idle bubble that was showing.
    push_companion_popup(state, text, "error" if state.notif_kind == "error" else "hot", now)
    # task.apply() above already finalised the selection over the fresh results, so
    # pin popup_selection_id to it: otherwise the very next frame's
    # update_companion_popups would treat the current study as a *new* selection
    # and clobber this verdict before it's ever drawn. The verdict now survives its
    # full TTL and is replaced only when the user navigates to a DIFFERENT study.
    state.popup_selection_id = _selected_study_id(state)
    return True


def companion_notification(state: "TuiState", now: float | None = None) -> str | None:
    """The one-line toast the theme companion announces, or ``None`` if expired.

    This is what tells the researcher a background search finished without them
    having to stare at the spinner; the message is owned by the active theme's
    companion so it stays in-character.
    """
    if not state.notif_text or state.notif_until <= 0:
        return None
    now = time.monotonic() if now is None else now
    if now >= state.notif_until:
        return None
    cfg = theme_config(state.theme)
    name = cfg.get("mascot_name", "Companion")
    icon = {
        "success": cfg.get("sparkle", "✳"),
        "error": "✗",
        "info": "•",
        "busy": "…",
    }.get(state.notif_kind, "•")
    return f"{icon} {name} — {state.notif_text}"


# Score bands → the companion's in-character reactions. Picked by score so the
# same study always draws the same remark (deterministic, unit-testable). The
# active theme's named companion delivers the line, so it stays in-voice.
REACTION_BANK: dict[str, list[str]] = {
    "hot": [
        "now THIS is a strong one — high-quality signal, worth your time",
        "excellent find — solid design and a high score, pin this one",
        "oh, top-shelf evidence here — I'd read this first",
    ],
    "solid": [
        "decent study, worth a look",
        "looks reasonable — middling score but it holds up",
        "okay-ish — keep it, read with a little salt",
    ],
    "weak": [
        "hmm, weak evidence on this one — read it skeptically",
        "careful — low score, shaky methodology here",
        "I'd be wary: high risk signals, treat as preliminary",
    ],
}


def score_band(label: str = "", score: int = 0) -> str:
    """Bucket a study into hot / solid / weak from its label (then score).

    The explicit RISK label wins over a high raw score — a study flagged risky
    is treated as weak regardless of where its arithmetic landed.
    """
    lbl = (label or "").upper()
    if lbl == "RISK":
        return "weak"
    if lbl == "HOT" or score >= 70:
        return "hot"
    if score < 45:
        return "weak"
    return "solid"


def companion_reaction(theme: str, label: str = "", score: int = 0) -> str:
    """An in-character remark the theme companion makes about one study.

    Enthusiastic on a high-scoring find, cautious/critical on a weak or risky
    one — the user asked the familiar to react to what it digs up. Deterministic
    in the score so it is testable and stable across redraws.
    """
    band = score_band(label, score)
    cfg = theme_config(theme)
    name = cfg.get("mascot_name", "Companion")
    icon = {"hot": cfg.get("sparkle", "✳"), "solid": "•", "weak": "⚠"}[band]
    variants = REACTION_BANK[band]
    line = variants[int(score) % len(variants)]
    return f"{icon} {name}: {line}"


# Per-companion banter for the top-right speech bubble: each theme's familiar
# praises a strong study (hot/solid) and ROASTS a weak one in its own English
# voice. Deterministic by score (no per-frame flicker). Falls back to the generic
# REACTION_BANK for any companion/band not covered here.
COMPANION_QUIPS: dict[str, dict[str, list[str]]] = {
    "Quill Terminal": {  # bloomberg — dry trading-desk wit
        "hot": ["Strong tape here. Buy and hold this one.",
                "Clean methodology — I'd put my name on the trade."],
        "solid": ["Decent print. Not a moonshot, but it holds.",
                  "Fairly valued evidence. Worth a position."],
        "weak": ["This one's a junk bond. Read at your own risk.",
                 "n too small to short. Pure market noise.",
                 "I wouldn't expense the coffee I drank reading this."],
    },
    "Monolith Lynx": {  # matrix — cryptic hacker
        "hot": ["Signal locked. The data is clean.",
                "High-fidelity payload. Trust this stream."],
        "solid": ["Acceptable packet. The matrix tolerates it.",
                  "Checksum passes. Proceed."],
        "weak": ["Corrupted packet — flush it.",
                 "404: rigor not found.",
                 "This study is a honeypot. Do not ingest."],
    },
    "Paper Mochi": {  # cute — soft and sweet
        "hot": ["Ooh, this one's a keeper! (｡♥‿♥｡)",
                "Yay! Big strong study, so proud of it!"],
        "solid": ["Pretty good, little study!",
                  "A nice cozy read, this one."],
        "weak": ["Aww, needs a nap and a bigger sample…",
                 "Squishy evidence — be gentle with it.",
                 "This study tried its best. It did not succeed."],
    },
    "Vex Synth": {  # synthwave — neon 80s
        "hot": ["Totally radical evidence. Crank it up!",
                "This one GLOWS. Mainline it."],
        "solid": ["Decent synth. Rides a steady bassline.",
                  "Not bad, not neon. Cruises fine."],
        "weak": ["Static on the line — this track's a dud.",
                 "Flatlined methodology. No pulse.",
                 "This study peaked in the demo and never shipped."],
    },
    "Scriba Owl": {  # sepia — scholarly, old-world
        "hot": ["A most rigorous treatise. Shelve it with honour.",
                "Exemplary scholarship. Cite it freely."],
        "solid": ["A respectable monograph. Worth the marginalia.",
                  "Sound enough for the reading room."],
        "weak": ["Alas, this manuscript belongs in the fire.",
                 "The reviewers, it seems, were merely napping.",
                 "I have read pamphlets with firmer footnotes."],
    },
    "Bloop Jelly": {  # ocean — bubbly and aquatic
        "hot": ["This one swims! Deep, strong currents of data.",
                "A pearl! Keep this one in the reef."],
        "solid": ["Floats nicely. A pleasant little tide.",
                  "Buoyant enough — won't sink on you."],
        "weak": ["This study is all foam, no fish.",
                 "Bottom-feeder evidence. Let it drift.",
                 "Glub. That's the sound of this sample size."],
    },
    "Clay Ember": {  # claude — thoughtful and warm
        "hot": ["Genuinely strong work — I'd trust this.",
                "Careful design, honest numbers. A real find."],
        "solid": ["Reasonable. Worth a considered read.",
                  "Holds together; just mind the caveats."],
        "weak": ["I want to like it, but the bias is doing the talking.",
                 "Confidently wrong is still wrong — skeptical here.",
                 "Brave of them to publish this, honestly."],
    },
}


def companion_quip(theme: str, label: str = "", score: int = 0) -> str:
    """A per-companion one-liner for the bubble — praise if good, a roast if weak.

    Deterministic in the score so the same study always draws the same quip
    (no flicker across redraws); falls back to the generic REACTION_BANK for
    companions/bands without bespoke banter.
    """
    band = score_band(label, score)
    name = theme_config(theme).get("mascot_name", "Companion")
    variants = (COMPANION_QUIPS.get(name) or {}).get(band) or REACTION_BANK[band]
    return variants[int(score) % len(variants)]


def companion_bubble_lines(theme: str, row: dict | None, width: int = 30) -> list[str]:
    """Wrapped speech-bubble text for the selected study, or [] if none.

    Pure + deterministic so it can be unit-tested and reused by render_demo:
    the bubble reacts to whichever study is selected (the keyboard TUI's "click").
    """
    if not row:
        return []
    quip = companion_quip(theme, str(row.get("label") or ""), int(row.get("final_score") or 0))
    return textwrap.wrap(quip, max(8, width - 4))[:4] or [""]


# Contextual one-liners the companion says when there's no study to react to,
# keyed by what the user is currently looking at.
_AMBIENT_LINES = {
    "empty": "nothing loaded yet — press s to search the literature",
    "watchlist": "browsing your tracked topics — Enter on a theme to refresh it",
    "saved": "your library — pick a study and I'll weigh in",
    "current": "fresh results up top — arrow through them and I'll comment",
    "evaluation": "appraisal view — I read the methodology so you don't have to",
}


def companion_line(state: "TuiState") -> str:
    """The companion's CURRENT remark — the single always-on voice channel.

    Recomputed every frame from live state, so the familiar visibly reacts to
    whatever the user is doing without any extra plumbing:
    - mid-search  → the latest streamed reaction (or a "digging" line);
    - just finished → the completion verdict (folds in the old toast);
    - browsing    → an in-character remark about the *selected* study (or the
      best one in view), enthusiastic on a strong score and wary on a weak one;
    - nothing loaded → a contextual nudge.

    This is what was missing: previously the companion only spoke in a transient
    panel/toast, so in normal browsing it appeared to react to nothing.
    """
    cfg = theme_config(state.theme)
    name = cfg.get("mascot_name", "Companion")
    if state.busy:
        task = getattr(state, "task", None)
        snap = task.progress.snapshot() if (task and task.progress) else {}
        if snap.get("reaction"):
            return str(snap["reaction"])
        return f"{cfg.get('sparkle', '✳')} {name}: digging through the literature…"
    # A fresh completion verdict wins for its short lifetime (was the bottom toast).
    toast = companion_notification(state)
    if toast:
        return toast
    # Browsing: react to the focused study, else the best one currently in view.
    row = selected_article(state)
    if not row:
        pool = _active_rows(state) or state.current_articles or state.saved_articles
        row = pool[0] if pool else None
    if row and (row.get("final_score") is not None or row.get("label")):
        return companion_reaction(state.theme, str(row.get("label") or ""),
                                  int(row.get("final_score") or 0))
    ambient = _AMBIENT_LINES.get(state.active_tab if (state.current_articles or state.saved_articles)
                                 else "empty", _AMBIENT_LINES["empty"])
    return f"• {name}: {ambient}"


# ── Event-driven companion pop-ups ──────────────────────────────────────────
# The bubble is now a transient POP-UP: it appears on an event (selecting a
# study, going idle, a search finishing or dragging on), lives at most
# POPUP_TTL seconds, and is interrupted (replaced) the moment the next event
# fires one. This is the interactive layer on top of the always-on header
# companion_line.
POPUP_TTL = 20.0          # a pop-up never lingers more than this
IDLE_SECS = 20.0          # no interaction for this long → a "you seem lost" nudge
SEARCH_LONG_SECS = 12.0   # a running search dragging past this → the first wait nudge
BUSY_NUDGE_EVERY = 18.0   # then keep the companion talking every ~this many seconds

# Playful nudges when the user has gone quiet ("si on semble perdu / prend du temps").
_IDLE_QUIPS = [
    "still there? pick a study and I'll dish the verdict",
    "lost in the stacks? press s to search, t to track a topic",
    "take your time — I'll be here judging methodology",
    "psst… ↑/↓ to browse, Enter on a watchlist to refresh it",
    "no rush. the literature isn't going anywhere",
]

# Recurring wait-banter while a long job runs, so the companion keeps the user
# company. Meta-analysis gets its own flavour (it really does take minutes).
_WAIT_QUIPS = [
    "still digging — good evidence takes a minute…",
    "sifting the literature, hang tight…",
    "reading the fine print so you don't have to…",
    "almost there — quality over speed…",
]
_META_WAIT_QUIPS = [
    "cross-checking effect sizes across studies…",
    "this is the careful part — heterogeneity matters…",
    "pooling confidence intervals, nearly there…",
    "weighing each study by its quality…",
    "synthesising the evidence — worth the wait…",
]


def push_companion_popup(state: "TuiState", text: str, band: str = "solid",
                         now: float | None = None, ttl: float = POPUP_TTL) -> None:
    """Raise a companion pop-up, interrupting whatever was showing.

    A fresh push always replaces the current bubble (the user asked that a new
    message cut off the previous one) and resets the ≤POPUP_TTL countdown.
    """
    now = time.monotonic() if now is None else now
    state.popup_text = text
    state.popup_band = band
    state.popup_born = now
    state.popup_until = now + min(ttl, POPUP_TTL)


def companion_popup(state: "TuiState", now: float | None = None) -> str | None:
    """The pop-up text if one is live, else None (expired/never-set)."""
    if not state.popup_text or state.popup_until <= 0:
        return None
    now = time.monotonic() if now is None else now
    return state.popup_text if now < state.popup_until else None


def note_interaction(state: "TuiState", now: float | None = None) -> None:
    """Record that the user just did something — resets the idle clock."""
    state.last_interaction = time.monotonic() if now is None else now
    state.idle_popped = False


def _selected_study_id(state: "TuiState") -> int:
    row = selected_article(state)
    try:
        return int((row or {}).get("id") or 0)
    except (TypeError, ValueError):
        return 0


def update_companion_popups(state: "TuiState", now: float | None = None) -> None:
    """Per-frame event engine that raises pop-ups. Pure (no curses), so the whole
    trigger matrix is unit-testable by driving it with crafted state + now values.

    Triggers, in priority order:
    - search/meta dragging on → recurring wait-banter every ~BUSY_NUDGE_EVERY s;
    - selecting a study       → a praise/roast quip about the now-selected study;
    - going idle              → a one-shot "you seem lost" nudge.
    """
    now = time.monotonic() if now is None else now
    cfg = theme_config(state.theme)
    name = cfg.get("mascot_name", "Companion")
    if state.busy:
        # Keep the companion talking through a long wait (meta especially), not
        # just once: fire at SEARCH_LONG_SECS, then every BUSY_NUDGE_EVERY s.
        due = SEARCH_LONG_SECS + state.busy_nudge_i * BUSY_NUDGE_EVERY
        if state.spinner_elapsed >= due:
            is_meta = bool(getattr(state, "task", None) and getattr(state.task, "kind", "") == "meta")
            bank = _META_WAIT_QUIPS if is_meta else _WAIT_QUIPS
            push_companion_popup(state, f"{name}: {bank[state.busy_nudge_i % len(bank)]}", "info", now)
            state.busy_nudge_i += 1
        return
    state.busy_nudge_i = 0  # re-arm for the next long job
    # Selecting a study (the keyboard TUI's "click") pops a fresh reaction.
    sid = _selected_study_id(state)
    if sid and sid != state.popup_selection_id:
        state.popup_selection_id = sid
        row = selected_article(state)
        band = score_band(str((row or {}).get("label") or ""), int((row or {}).get("final_score") or 0))
        quip = companion_quip(state.theme, str((row or {}).get("label") or ""), int((row or {}).get("final_score") or 0))
        push_companion_popup(state, f"{name}: {quip}", band, now)
        return
    # Otherwise, if the user has gone quiet, nudge them once.
    if not state.idle_popped and state.last_interaction and (now - state.last_interaction) > IDLE_SECS:
        state.idle_popped = True
        quip = _IDLE_QUIPS[state.idle_quip_i % len(_IDLE_QUIPS)]
        state.idle_quip_i += 1
        push_companion_popup(state, f"{name}: {quip}", "info", now)


def load_state(db_path: str | Path = DEFAULT_DB_PATH, limit: int = 100) -> TuiState:
    store = ResearchStore(db_path)
    try:
        saved = sort_articles_for_ui(store.list_articles(limit=limit), mode="recent_relevant")
        state = TuiState(
            db_path=Path(db_path),
            articles=[],
            current_articles=[],
            saved_articles=saved,
            watchlists=store.list_watchlists(),
            meta_analyses=store.list_meta_analyses(),
        )
        return state
    finally:
        store.close()


def open_selected_meta(state: TuiState) -> None:
    """Load the highlighted meta-analysis into the reading pane (resets scroll)."""
    metas = state.meta_analyses
    if not metas:
        state.meta_document = ""
        state.meta_query = ""
        state.meta_scroll = 0
        return
    state.selected_meta = clamp_index(state.selected_meta, len(metas))
    meta = metas[state.selected_meta]
    state.meta_document = str(meta.get("document_md") or "")
    state.meta_query = str(meta.get("query") or "")
    state.meta_scroll = 0


def enter_reading(state: TuiState) -> bool:
    """Enter full-detail reading for the selected study (the Enter key).

    Returns True when reading actually started. No-op (returns False) on the
    Meta tab — which has its own document reader — when the article list isn't
    focused, or when no study is selected.
    """
    if state.active_tab == "meta" or state.focus != "articles":
        return False
    if not selected_article(state):
        return False
    state.reading = True
    state.detail_scroll = 0
    return True


def exit_reading(state: TuiState) -> None:
    """Leave reading mode and reset the detail scroll."""
    state.reading = False
    state.detail_scroll = 0


def _detail_pane_title(state: TuiState) -> str:
    """Title for the right-hand detail box, with a reading-mode marker.

    The ' — READING ↑/↓' suffix shows on *every* article tab while reading (incl.
    a watchlist theme's studies), not just current/saved/evaluation.
    """
    if state.active_tab == "watchlist":
        title = "WATCHLIST DETAIL"
    elif state.active_tab == "meta":
        title = "META-ANALYSIS"
    else:
        title = "CRITICAL APPRAISAL" if state.active_tab == "evaluation" else "ARTICLE DETAIL"
    if state.reading:
        title += " — READING ↑/↓"
    return title


def reading_handle_key(state: TuiState, key: int) -> None:
    """Handle one key while reading a study's full detail: scroll, page, or exit.

    Esc / Enter / q leave; ↑/↓ scroll a line; PgUp/PgDn/Space page. The upper
    bound is clamped at draw time (where the wrapped line count is known), so
    here we only need to guard the lower bound.
    """
    if key in (27, 10, 13, curses.KEY_ENTER, ord("q"), ord("Q")):
        exit_reading(state)
    elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
        state.detail_scroll += 1
    elif key in (curses.KEY_UP, ord("k"), ord("K")):
        state.detail_scroll = max(0, state.detail_scroll - 1)
    elif key in (curses.KEY_NPAGE, ord(" ")):
        state.detail_scroll += PAGE_LINES
    elif key == curses.KEY_PPAGE:
        state.detail_scroll = max(0, state.detail_scroll - PAGE_LINES)
    # Any other key is ignored while reading (the mode is modal).


def _parse_year(row: dict) -> int:
    import re
    for key in ("publication_date", "date", "year"):
        m = re.search(r"(19|20)\d{2}", str(row.get(key) or ""))
        if m:
            return int(m.group(0))
    return 0


def _hydrate_deep_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backfill `deep_summary`/`summary_method` from each row's stored `raw_json`.

    The articles table has no dedicated column for the AI synthesis, but
    `upsert_article` already tucks it inside `raw_json`. Re-exposing it here means
    deep-searched studies still light up their AI DEEP SUMMARY after a reload or
    in a fresh session — not just on the freshly-injected current-results rows.
    Rows are mutated in place; missing/malformed `raw_json` is silently skipped.
    """
    for row in rows:
        if row.get("deep_summary"):
            continue
        raw = row.get("raw_json")
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if isinstance(data, dict) and data.get("deep_summary"):
            row["deep_summary"] = data["deep_summary"]
            if not row.get("summary_method"):
                row["summary_method"] = data.get("summary_method") or ""
    return rows


def sort_articles_for_ui(rows: list[dict[str, Any]], mode: str = "recent_relevant") -> list[dict[str, Any]]:
    _hydrate_deep_fields(rows)
    if mode == "score":
        return sorted(rows, key=lambda r: (int(r.get("final_score") or 0), -int(r.get("risk_score") or 0)), reverse=True)
    if mode == "recent":
        return sorted(rows, key=lambda r: _parse_year(r), reverse=True)
    # Recent but not stupid: privilege recency + score, penalize risk.
    return sorted(
        rows,
        key=lambda r: (_parse_year(r) * 2 + int(r.get("final_score") or 0) - int(r.get("risk_score") or 0) * 3),
        reverse=True,
    )


def _authors(row: dict) -> str:
    raw = row.get("authors_json") or "[]"
    try:
        authors = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        authors = []
    if not authors:
        return "—"
    return ", ".join(str(a) for a in authors[:5])


def selected_article(state: TuiState) -> dict | None:
    if state.active_tab == "meta":
        return None  # the meta tab selects a document, never an article row
    if state.active_tab == "saved":
        source = state.saved_articles
    elif state.active_tab == "evaluation":
        source = state.current_articles or state.saved_articles
    elif state.active_tab == "watchlist":
        source = state.watchlist_articles
    else:
        source = state.current_articles
    if not source:
        return None
    idx = max(0, min(state.selected_article, len(source) - 1))
    return source[idx]


def selected_watchlist(state: TuiState) -> dict | None:
    if not state.watchlists:
        return None
    idx = max(0, min(state.selected_watchlist, len(state.watchlists) - 1))
    return state.watchlists[idx]


def load_watchlist_articles(state: TuiState, limit: int = 100) -> list[dict[str, Any]]:
    watch = selected_watchlist(state)
    if not watch:
        state.watchlist_articles = []
        return []
    store = ResearchStore(state.db_path)
    try:
        rows = sort_articles_for_ui(store.list_watchlist_articles(int(watch["id"]), limit=limit), mode="recent_relevant")
    finally:
        store.close()
    state.watchlist_articles = rows
    state.selected_article = clamp_index(state.selected_article, len(rows))
    return rows


def refresh_state(state: TuiState, limit: int = 100) -> None:
    fresh = load_state(state.db_path, limit=limit)
    state.saved_articles = fresh.saved_articles
    state.watchlists = fresh.watchlists
    state.meta_analyses = fresh.meta_analyses
    state.selected_meta = clamp_index(state.selected_meta, len(state.meta_analyses))
    if not state.current_articles and state.active_tab == "current":
        state.articles = []
    elif state.active_tab == "watchlist":
        load_watchlist_articles(state, limit=limit)
        state.articles = state.watchlist_articles
    else:
        state.articles = state.current_articles if state.active_tab in {"current", "evaluation"} else state.saved_articles
    state.selected_article = clamp_index(state.selected_article, len(state.articles))
    state.selected_watchlist = clamp_index(state.selected_watchlist, len(state.watchlists))


def build_recent_domain_query(domain: str, days: int = 30) -> str:
    domain = " ".join(str(domain or "").split()) or "science"
    return f"{domain} last {days} days recent research OR latest study"


def _deep_eval_from_row(row: dict) -> dict:
    raw = row.get("explanation_json") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        parsed = {}
    if isinstance(parsed, dict) and parsed.get("deep_evaluation"):
        return parsed["deep_evaluation"]
    return scoring.evaluate_article_deep(row)


def follow_topic_from_tui(state: TuiState, name: str, query: str, sources: list[str] | None = None, lang: str = "fr") -> str:
    sources = sources or ["pubmed", "openalex", "crossref"]
    store = ResearchStore(state.db_path)
    try:
        watchlist_id = store.add_watchlist(name, query, sources, lang)
    finally:
        store.close()
    refresh_state(state)
    for idx, row in enumerate(state.watchlists):
        if int(row.get("id") or 0) == int(watchlist_id):
            state.selected_watchlist = idx
            break
    load_watchlist_articles(state)
    state.active_tab = "watchlist"
    state.focus = "watchlists"
    state.status = f"Following topic: {name} — press Enter or run search to populate studies"
    return state.status


def topic_slug(text: str, limit: int = 24) -> str:
    """Slugify a subject into a default topic name (lowercase, dash-joined words)."""
    words = re.findall(r"[A-Za-z0-9]+", str(text or "").lower())
    return ("-".join(words)[:limit].strip("-")) or "topic"


def begin_topic_prompt(state: TuiState) -> None:
    """Start creating a topic by asking for the SEARCH first.

    The first field IS the underlying search — the only text ever sent to the
    sources when the topic is refreshed. The name is asked second and pre-filled
    from it (so it adapts to the subject), and is a pure label: it is never part
    of the query. The labels spell this out so a name typed into the first field
    can't silently become the search.
    """
    default = state.last_query or str((selected_article(state) or {}).get("title", "") or "")[:60]
    begin_prompt(state, "topic_subject", "Search to track", default)


def topic_subject_entered(state: TuiState, subject: str) -> None:
    """Search captured — stash it and ask for a name (a label, never searched)."""
    state.pending_topic_subject = subject
    begin_prompt(state, "topic_name", "Topic name (label only)", topic_slug(subject))


def topic_name_entered(state: TuiState, name: str) -> str:
    """Name confirmed — create the topic.

    The name is purely a label: the search is ALWAYS the subject captured first,
    never the name. (Previously this fell back to ``… or name`` for the subject,
    which could quietly turn the label into the searched query.)
    """
    subject = state.pending_topic_subject
    state.pending_topic_subject = ""
    final_name = name.strip() or topic_slug(subject)
    return follow_topic_from_tui(state, final_name, subject, lang=resolve_search_config(state)["lang"])


def begin_edit_selected_topic(state: TuiState) -> str:
    """Open the rename / change-subject prompt for the highlighted topic.

    Stashes the topic id on the state so the two-step prompt (new name → new
    subject) can UPDATE the existing row by id instead of creating a new topic.
    """
    watch = selected_watchlist(state)
    if not watch:
        state.status = "No topic to edit — press t to create one"
        return state.status
    state.editing_watchlist_id = int(watch.get("id") or 0)
    begin_prompt(state, "edit_topic_name", "New topic name (label only)", str(watch.get("name") or ""))
    return state.status


def apply_topic_edit(state: TuiState, name: str, query: str) -> str:
    """Commit a topic rename + new subject (called once both prompts are filled)."""
    wid = int(state.editing_watchlist_id or 0)
    state.editing_watchlist_id = 0
    if not wid:
        state.status = "No topic selected for edit"
        return state.status
    store = ResearchStore(state.db_path)
    try:
        ok = store.update_watchlist(wid, name=name, query=query)
    except Exception as exc:  # most likely a name collision (UNIQUE constraint)
        store.close()
        state.status = f"Rename failed (name already used?): {exc}"
        return state.status
    store.close()
    refresh_state(state)
    for idx, row in enumerate(state.watchlists):
        if int(row.get("id") or 0) == wid:
            state.selected_watchlist = idx
            break
    if state.active_tab == "watchlist":
        load_watchlist_articles(state)
    _sync_articles_view(state)
    state.status = (f"Topic updated: {name} — subject « {query[:48]} »" if ok
                    else "Topic no longer exists")
    return state.status


def delete_selected_topic(state: TuiState) -> str:
    """Delete the highlighted topic (and its membership links). Articles are kept."""
    watch = selected_watchlist(state)
    if not watch:
        state.status = "No topic to delete — press t to create one"
        return state.status
    wid = int(watch.get("id") or 0)
    name = str(watch.get("name") or "topic")
    store = ResearchStore(state.db_path)
    try:
        deleted = store.delete_watchlist(wid)
    finally:
        store.close()
    refresh_state(state)
    state.selected_watchlist = clamp_index(state.selected_watchlist, len(state.watchlists))
    if state.active_tab == "watchlist":
        load_watchlist_articles(state)
    _sync_articles_view(state)
    state.status = f"Deleted topic: {name}" if deleted else f"Topic already absent: {name}"
    return state.status


def _record_pdf_status(db_path: str | Path, article_id: int, result: dict) -> None:
    store = ResearchStore(db_path)
    try:
        from normalization import now_iso
        store.conn.execute(
            """
            INSERT INTO pdfs(article_id, pdf_url, local_path, extraction_status, full_text_path, checksum, updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(article_id) DO UPDATE SET local_path=excluded.local_path,
                extraction_status=excluded.extraction_status, updated_at=excluded.updated_at
            """,
            (
                article_id,
                result.get("url", ""),
                result.get("path", ""),
                "downloaded" if result.get("success") else "failed",
                "",
                "",
                now_iso(),
            ),
        )
        store.conn.commit()
    finally:
        store.close()


def download_selected_article(state: TuiState, downloader=pdf_sender.download_pdf) -> str:
    row = selected_article(state)
    if not row:
        state.status = "No selected article to download"
        return state.status
    doi = row.get("doi") or ""
    if not doi:
        state.status = "Selected article has no DOI — PDF download impossible"
        return state.status
    result = downloader(doi, title=row.get("title") or "")
    _record_pdf_status(state.db_path, int(row["id"]), result)
    if result.get("success") and result.get("path"):
        state.status = f"PDF ready via {result.get('method', 'unknown')}: {result['path']}"
    else:
        state.status = f"PDF download failed: {result.get('error', 'unknown error')}"
    return state.status


def run_meta_analysis_from_tui(state: TuiState, query: str, max_articles: int = 8,
                               runner=meta_analysis.perform_meta_analysis, lang: str = "fr",
                               meta_sources: "list[str] | None" = None,
                               analysis_depth: str = "medium",
                               progress_cb: "Callable[..., None] | None" = None,
                               result_cb: "Callable[..., None] | None" = None) -> str:
    # Drive the staged progress band for the (long) meta run by translating the
    # runner's human status lines into META_STAGES keys; stream each included
    # study as it's scored. The runner's kwargs are only passed when its
    # signature accepts them (test/legacy runners are called the plain way).
    def _meta_progress(msg) -> None:
        if not progress_cb:
            return
        m = str(msg)
        if "Compil" in m:
            progress_cb("synthesize", "writing the synthesis", 0, 0)
        elif m.startswith("PDF"):
            mm = re.search(r"(\d+)\s*/\s*(\d+)", m)
            cur, tot = (int(mm.group(1)), int(mm.group(2))) if mm else (0, 0)
            detail = m.split("—", 1)[1].strip() if "—" in m else "reading full text"
            progress_cb("extract", detail, cur, tot)
        elif m.startswith("MiniMax"):
            mm = re.search(r"(\d+)\s*/\s*(\d+)", m)
            cur, tot = (int(mm.group(1)), int(mm.group(2))) if mm else (0, 0)
            detail = m.split("—", 1)[1].strip() if "—" in m else "AI enrichment"
            progress_cb("extract", detail, cur, tot)
        else:
            progress_cb("collect", "searching databases", 0, 0)

    try:
        rparams = inspect.signature(runner).parameters
        _any_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in rparams.values())
        accepts_progress = "progress" in rparams or _any_varkw
        accepts_sources = "sources" in rparams or _any_varkw
        accepts_depth = "analysis_depth" in rparams or _any_varkw
    except (TypeError, ValueError):
        accepts_progress = False
        accepts_sources = False
        accepts_depth = False

    if progress_cb:
        progress_cb("collect", "searching databases", 0, 0)
    call_kwargs: dict[str, Any] = {"max_articles": max_articles, "lang": lang}
    if accepts_progress:
        call_kwargs["progress"] = _meta_progress
    if accepts_sources and meta_sources:
        call_kwargs["sources"] = meta_sources
    if accepts_depth:
        call_kwargs["analysis_depth"] = analysis_depth
    result = runner(query, **call_kwargs)
    articles = result.get("articles", []) or []
    document = str(result.get("summary") or "")
    store = ResearchStore(state.db_path)
    ids: list[int] = []
    meta_id = 0
    md_path = ""
    n = len(articles)
    try:
        for i, article in enumerate(articles, 1):
            if progress_cb:
                progress_cb("score", "scoring & storing", i, n)
            payload = dict(article)
            payload.setdefault("source", "meta-analysis")
            payload.setdefault("type", "meta_included_study")
            aid = store.upsert_article(payload, query=query)
            row = store.get_article(aid) or payload
            score = scoring.score_article(row)
            store.upsert_score(aid, score)
            store.add_summary(aid, "meta-analysis", result.get("lang", lang), short=document[:1000], structured={"meta_summary": document, "n_studies": result.get("n_studies", len(articles))}, raw=document)
            ids.append(aid)
            # Stream each included study into the progress band, with the
            # companion's reaction — same as the search path.
            if result_cb:
                fscore = int(score.get("final_score") or 0)
                flabel = str(score.get("label") or "")
                result_cb(str(payload.get("title") or row.get("title") or ""),
                          fscore, flabel, companion_reaction(state.theme, flabel, fscore))
        # Persist the compiled document ONCE (its own record + a readable .md on
        # disk), but only when generation actually produced text — never write a
        # blank document that would look exactly like the bug we're fixing.
        if document.strip():
            from normalization import now_iso
            created_at = now_iso()
            try:
                import file_writer
                md_path = file_writer.write_meta_markdown(query, document, created_at)
            except Exception:
                md_path = ""
            meta_id = store.add_meta_analysis(
                query=query, document_md=document, lang=result.get("lang", lang),
                n_studies=int(result.get("n_studies", n) or n),
                depth=str(result.get("depth") or ""), md_path=md_path, created_at=created_at)
    finally:
        store.close()
    refresh_state(state)
    fresh_rows = [row for row in state.saved_articles if int(row.get("id") or 0) in set(ids)]
    state.current_articles = fresh_rows or state.saved_articles[:len(ids)]
    if document.strip() and meta_id:
        # Land on the Meta-Analyses tab with the freshly-compiled document open.
        # Select by the inserted id, not list position — a non-monotonic wall
        # clock could otherwise sort an older run to index 0.
        state.selected_meta = next(
            (i for i, m in enumerate(state.meta_analyses) if int(m.get("id") or 0) == meta_id), 0)
        open_selected_meta(state)
        state.active_tab = "meta"
        state.focus = "articles"
        where = f" — saved to {md_path}" if md_path else ""
        state.status = f"Meta-analysis ready: {query} ({len(ids)} studies){where}"
    else:
        # Generation returned no narrative — keep the included studies in view.
        state.active_tab = "evaluation"
        state.status = f"Meta-analysis produced no readable document for {query}"
    _sync_articles_view(state)
    return state.status


def _runner_accepts_progress_cb(runner: "Callable[..., Any]") -> bool:
    """True if ``runner`` declares a ``progress_cb`` parameter (or **kwargs).

    Lets us pass the progress channel to runners that support it while calling
    legacy/test runners the old way — without resorting to a catch-all
    ``except TypeError`` that would mask real errors and double-run the search.
    """
    try:
        params = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.name == "progress_cb" or p.kind == inspect.Parameter.VAR_KEYWORD
        for p in params
    )


def search_and_store_from_tui(
    state: TuiState,
    query: str,
    max_results: int = 5,
    sources: list[str] | None = None,
    lang: str = "fr",
    deep: bool = False,
    allow_scihub: bool | None = None,
    runner=run_search,
    progress_cb: "Callable[..., None] | None" = None,
    result_cb: "Callable[..., None] | None" = None,
) -> str:
    sources = sources or ["pubmed", "openalex", "crossref"]
    log_buffer = io.StringIO()
    with contextlib.redirect_stdout(log_buffer), contextlib.redirect_stderr(log_buffer):
        # Forward progress reporting only to runners whose signature accepts it
        # (the deep pipeline does); older/test runners are called the legacy way.
        # We inspect the signature instead of catching TypeError so a genuine
        # TypeError raised *inside* the runner is never silently swallowed and
        # the whole search re-run (doubling network/API calls).
        if _runner_accepts_progress_cb(runner):
            articles = runner(query, max_results, lang, sources, deep, allow_scihub,
                              progress_cb=progress_cb)
        else:
            articles = runner(query, max_results, lang, sources, deep, allow_scihub)
    store = ResearchStore(state.db_path)
    article_ids: list[int] = []
    current_rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    n_articles = len(articles)
    try:
        for i, article in enumerate(articles, 1):
            if progress_cb:
                progress_cb("evaluate", "scoring & storing", i, n_articles)
            payload = article.__dict__ if hasattr(article, "__dict__") else dict(article)
            aid = store.upsert_article(payload, query=query)
            row = store.get_article(aid) or payload
            score = scoring.score_article(row)
            store.upsert_score(aid, score)
            article_ids.append(aid)
            # Stream the freshly-scored study to the UI, with the companion's
            # in-character reaction (enthusiastic on a strong find, wary on a
            # weak one). result_cb is the ProgressChannel sink; absent in tests
            # that don't care, so it's always optional.
            if result_cb:
                fscore = int(score.get("final_score") or 0)
                flabel = str(score.get("label") or "")
                result_cb(str(payload.get("title") or row.get("title") or ""),
                          fscore, flabel, companion_reaction(state.theme, flabel, fscore))
            # The deep (full-text + AI) path produces a rich `deep_summary` /
            # critical appraisal. `upsert_article` already stores it inside
            # `raw_json` (and `_hydrate_deep_fields` re-exposes it on reload), so
            # this `add_summary` is the *structured* record — model + lang +
            # timestamp — mirroring the meta-analysis path. We also carry the text
            # on the in-memory row so the detail view surfaces it immediately.
            deep_text = str(payload.get("deep_summary") or "")
            if deep_text:
                store.add_summary(
                    aid, "deep-research", lang,
                    short=deep_text[:1500],
                    structured={"summary_method": payload.get("summary_method") or "",
                                "kind": "deep_research"},
                    raw=deep_text,
                )
            # Collect every freshly stored study by id (dedup, keep search order).
            # Pulling rows straight from their ids avoids the old top-100 filter
            # below, which silently dropped results scoring outside the library top.
            if aid not in seen_ids:
                seen_ids.add(aid)
                fresh_row = store.get_article(aid) or row
                if deep_text:
                    fresh_row = dict(fresh_row)
                    fresh_row["deep_summary"] = deep_text
                    fresh_row["summary_method"] = payload.get("summary_method") or ""
                current_rows.append(fresh_row)
        watch = selected_watchlist(state)
        if state.active_tab == "watchlist" and watch:
            store.record_watchlist_hits(int(watch["id"]), article_ids)
    finally:
        store.close()
    state.last_query = query
    fresh = load_state(state.db_path)
    state.saved_articles = fresh.saved_articles
    state.watchlists = fresh.watchlists
    state.current_articles = current_rows
    if state.active_tab == "watchlist" and watch:
        load_watchlist_articles(state)
        state.articles = state.watchlist_articles
        state.focus = "articles" if state.watchlist_articles else "watchlists"
    else:
        state.articles = state.current_articles
        state.active_tab = "current"
        state.focus = "articles"
    state.selected_article = 0
    n_found = len(current_rows)
    plural = "s" if n_found != 1 else ""
    state.status = f"{n_found} étude{plural} trouvée{plural} — UI nettoyée, détails prêts"
    return f"{n_found} article{plural} stored for {query}"


def make_search_task(
    state: "TuiState",
    raw_query: str,
    *,
    verb: str,
    max_results: int,
    sources: list[str] | None = None,
    meta_sources: "list[str] | None" = None,
    lang: str = "fr",
    deep: bool = False,
    allow_scihub: bool | None = None,
    kind: str = "search",
    recent: bool = False,
    meta: bool = False,
    meta_depth: str = "medium",
) -> "BackgroundTask":
    """Build a :class:`BackgroundTask` that searches on a *scratch* ``TuiState``.

    This honours BackgroundTask's invariant — the worker never touches the live
    state. It rebuilds an isolated ``TuiState`` from the same DB (its own SQLite
    connection, the threading discipline ``run_with_spinner`` already documents),
    runs the existing, tested search/meta routine there, and returns that scratch
    state. ``apply`` then copies the results onto the live state on the UI thread
    and returns the English line the companion announces when the search ends.
    Navigation context (active tab, selected watchlist) is snapshotted now so a
    user browsing mid-search still gets a coherent result.
    """
    db_path = state.db_path
    theme = state.theme
    snap_tab = state.active_tab
    live_watch = selected_watchlist(state)
    snap_watch_id = int(live_watch.get("id") or 0) if live_watch else 0
    query = build_recent_domain_query(raw_query, days=30) if recent else raw_query
    # The worker writes stage updates here; the UI reads them to paint the staged
    # "ultracode" progress panel. Shared by reference, attached to the task below.
    progress = ProgressChannel()

    def worker() -> "TuiState":
        scratch = load_state(db_path)
        scratch.theme = theme
        scratch.active_tab = snap_tab
        if snap_watch_id:
            for i, wl in enumerate(scratch.watchlists):
                if int(wl.get("id") or 0) == snap_watch_id:
                    scratch.selected_watchlist = i
                    break
        if meta:
            run_meta_analysis_from_tui(scratch, query, max_articles=max_results, lang=lang,
                                       meta_sources=meta_sources, analysis_depth=meta_depth,
                                       progress_cb=progress.report, result_cb=progress.add_result)
        else:
            search_and_store_from_tui(
                scratch, query, max_results=max_results, sources=sources,
                lang=lang, deep=deep, allow_scihub=allow_scihub,
                progress_cb=progress.report, result_cb=progress.add_result,
            )
        return scratch

    def apply(live: "TuiState", scratch: "TuiState") -> str:
        live.current_articles = scratch.current_articles
        live.saved_articles = scratch.saved_articles
        live.watchlists = scratch.watchlists
        live.watchlist_articles = scratch.watchlist_articles
        # The worker re-listed meta-analyses AFTER persisting the new one, so this
        # fresh snapshot already includes a just-completed run.
        live.meta_analyses = scratch.meta_analyses
        live.last_query = scratch.last_query or query
        live.active_tab = scratch.active_tab
        live.focus = scratch.focus
        live.selected_article = 0
        if meta:
            # Carry the open document across only for a meta run — a normal search
            # leaves these empty and would otherwise wipe a doc the user had open.
            live.selected_meta = scratch.selected_meta
            live.meta_document = scratch.meta_document
            live.meta_query = scratch.meta_query
            live.meta_scroll = scratch.meta_scroll
        else:
            live.selected_meta = clamp_index(live.selected_meta, len(live.meta_analyses))
        if scratch.active_tab == "watchlist" and snap_watch_id:
            for i, wl in enumerate(live.watchlists):
                if int(wl.get("id") or 0) == snap_watch_id:
                    live.selected_watchlist = i
                    break
        _sync_articles_view(live)
        n = len(scratch.current_articles)
        unit = "study" if n == 1 else "studies"
        verb_word = "analyzed" if meta else "found"
        deep_note = " · deep AI synthesis" if deep else ""
        # Companion's verdict on the best find — the toast reacts to the haul.
        best = max((int(a.get("final_score") or 0) for a in scratch.current_articles), default=0)
        best_label = next((str(a.get("label") or "") for a in scratch.current_articles
                           if int(a.get("final_score") or 0) == best), "") if n else ""
        verdict = f" · best {score_band(best_label, best)} ({best})" if n else ""
        live.status = f"{n} {unit} ready for '{query[:48]}'{deep_note}"
        return f"{verb_word} {n} {unit} for '{query[:40]}'{deep_note}{verdict}"

    return BackgroundTask(kind=kind, verb=verb, query=raw_query, worker=worker,
                          apply=apply, progress=progress)


def evaluate_selected_article(state: TuiState) -> str:
    row = selected_article(state)
    if not row:
        state.status = "No article selected"
        return state.status
    store = ResearchStore(state.db_path)
    try:
        score = scoring.score_article(row)
        store.upsert_score(int(row["id"]), score)
    finally:
        store.close()
    refresh_state(state)
    state.active_tab = "evaluation"
    state.articles = state.current_articles if state.current_articles else state.saved_articles
    state.status = f"Evaluated article #{row.get('id')}"
    return state.status


def delete_selected_saved_article(state: TuiState) -> str:
    if state.active_tab != "saved":
        state.status = "Delete works from SAVED only — switch to Saved first"
        return state.status
    row = selected_article(state)
    if not row:
        state.status = "No saved study selected"
        return state.status
    article_id = int(row.get("id") or 0)
    title = str(row.get("title") or "selected study")
    store = ResearchStore(state.db_path)
    try:
        deleted = store.delete_article(article_id)
    finally:
        store.close()
    refresh_state(state)
    state.active_tab = "saved"
    _sync_articles_view(state)
    state.status = f"Deleted saved study: {title[:72]}" if deleted else "Saved study already absent"
    return state.status


def clear_saved_articles(state: TuiState) -> str:
    store = ResearchStore(state.db_path)
    try:
        count = store.clear_articles()
    finally:
        store.close()
    state.current_articles = []
    state.watchlist_articles = []
    state.selected_article = 0
    refresh_state(state)
    state.active_tab = "saved"
    _sync_articles_view(state)
    state.status = f"Cleared {count} saved studies"
    return state.status


def add_selected_to_watchlist(state: TuiState) -> str:
    """Attach the selected article to the highlighted watchlist theme.

    Works from any article tab; the target is whichever theme is highlighted in
    the sidebar (``state.selected_watchlist``). Idempotent — a second press just
    re-touches the existing hit. The status names the theme so the user always
    knows where the study landed.
    """
    watch = selected_watchlist(state)
    if not watch:
        state.status = "No watchlist yet — press t to create one"
        return state.status
    row = selected_article(state)
    if not row:
        state.status = "No article selected"
        return state.status
    article_id = int(row.get("id") or 0)
    if not article_id:
        state.status = "Article not saved yet — cannot add to watchlist"
        return state.status
    name = str(watch.get("name") or "watchlist")
    title = str(row.get("title") or "selected study")
    store = ResearchStore(state.db_path)
    try:
        new_hits = store.record_watchlist_hits(int(watch["id"]), [article_id])
    finally:
        store.close()
    if state.active_tab == "watchlist":
        load_watchlist_articles(state)
    _sync_articles_view(state)
    verb = "Added to" if new_hits else "Already in"
    state.status = f"{verb} watchlist '{name}': {title[:64]}"
    return state.status


def remove_selected_from_watchlist(state: TuiState) -> str:
    """Detach the selected article from the highlighted watchlist theme.

    Only the membership link is dropped; the study itself stays in the database.
    On the Watchlist tab the list is reloaded so the row disappears immediately.
    """
    watch = selected_watchlist(state)
    if not watch:
        state.status = "No watchlist yet — press t to create one"
        return state.status
    row = selected_article(state)
    if not row:
        state.status = "No article selected"
        return state.status
    article_id = int(row.get("id") or 0)
    if not article_id:
        state.status = "Article not saved yet — nothing to remove"
        return state.status
    name = str(watch.get("name") or "watchlist")
    title = str(row.get("title") or "selected study")
    store = ResearchStore(state.db_path)
    try:
        removed = store.remove_watchlist_hit(int(watch["id"]), article_id)
    finally:
        store.close()
    if state.active_tab == "watchlist":
        load_watchlist_articles(state)
    _sync_articles_view(state)
    state.status = (f"Removed from watchlist '{name}': {title[:60]}" if removed
                    else f"Not in watchlist '{name}': {title[:60]}")
    return state.status


def render_detail_text(state: TuiState, width: int = 100) -> str:
    row = selected_article(state)
    if not row:
        if state.active_tab == "current":
            return "Aucun résultat courant. Appuie sur s pour rechercher, ou va dans SAVED ARTICLES."
        if state.active_tab == "watchlist":
            return "Aucune étude pour cette watchlist. Sélectionne un thème puis appuie sur Enter pour chercher."
        return "Aucun article stocké. Lance d'abord une recherche ou suis un sujet."
    ev = _deep_eval_from_row(row)  # compute the deep evaluation once, not 8×
    methodology = ev.get("methodology", {})
    funding = ev.get("funding", {})
    lines = [
        f"#{row.get('id')}  {row.get('label') or 'WATCH'} {row.get('final_score') or 0}  {row.get('title')}",
        f"Evidence: {row.get('evidence_score') or 0} | Novelty: {row.get('novelty_score') or 0} | Clinical: {row.get('clinical_relevance_score') or 0} | Risk: {row.get('risk_score') or 0}",
        f"Method: {methodology.get('study_design', 'unclear')} | Blinding: {methodology.get('blinding', 'not_reported')} | Control: {methodology.get('control', 'not_reported')}",
        f"Funding: {', '.join(funding.get('suspected_funders', []) or []) or ('detected' if funding.get('funding_detected') else 'not reported')} | Conflict risk: {ev.get('conflicts', {}).get('conflict_risk_score', 0)}",
        f"Red flags: {', '.join(ev.get('red_flags', []) or []) or '—'}",
        f"Journal: {row.get('journal') or '—'} | Date: {row.get('publication_date') or '—'} | Source: {row.get('source') or '—'}",
        f"Auteurs: {_authors(row)}",
        f"DOI: {row.get('doi') or '—'}",
        f"URL: {row.get('url') or '—'}",
    ]
    # Deep (full-text + AI) searches attach a rich synthesis + critical appraisal;
    # surface it above the raw abstract so the AI work is actually visible.
    deep_summary = str(row.get("deep_summary") or "")
    if deep_summary:
        lines.append("")
        lines.append("AI DEEP SUMMARY")
        method = str(row.get("summary_method") or "")
        if method:
            lines.append(f"[{method}]")
        for para in deep_summary.splitlines() or ["—"]:
            lines.extend(textwrap.wrap(para, width=max(30, width - 4)) or [""])
    lines.append("")
    lines.append("ABSTRACT")
    abstract = row.get("abstract") or "—"
    for para in str(abstract).splitlines() or ["—"]:
        lines.extend(textwrap.wrap(para, width=max(30, width - 4)) or [""])
    return "\n".join(lines)


def render_watchlist_text(state: TuiState, width: int = 100) -> str:
    if not state.watchlists:
        return "Aucune watchlist. Appuie sur T pour suivre un thème."
    lines = ["WATCHLIST THEMES"]
    for i, row in enumerate(state.watchlists):
        marker = "▶" if i == state.selected_watchlist else " "
        try:
            sources = ",".join(json.loads(row.get("sources_json") or "[]"))
        except Exception:
            sources = ""
        last_run = row.get("last_run_at") or "jamais"
        lines.append(f"{marker} #{row.get('id')} {row.get('name')} · {row.get('query')} · {sources} · last:{last_run}")
    watch = selected_watchlist(state)
    lines.append("")
    if watch:
        lines.append(f"SELECTED THEME: {watch.get('name')} — {watch.get('query')}")
        if state.watchlist_articles:
            lines.append("STUDIES FOR THIS THEME")
            for idx, article in enumerate(state.watchlist_articles[:10], 1):
                lines.append(f"{idx:02d} {article.get('label') or 'WATCH':<5} {article.get('final_score') or 0:>3} {str(article.get('title') or '')[:70]}")
        else:
            lines.append("Aucune étude liée pour l'instant. Entrée lance/rafraîchit la recherche du thème.")
    return "\n".join(
        wrapped
        for line in lines
        for wrapped in (textwrap.wrap(line, width=max(30, width - 2)) or [""])
    )


def render_evaluation_text(state: TuiState, width: int = 100) -> str:
    row = selected_article(state)
    if not row:
        return "Aucun article sélectionné pour évaluation."
    ev = _deep_eval_from_row(row)
    method = ev.get("methodology", {})
    funding = ev.get("funding", {})
    conflicts = ev.get("conflicts", {})
    relevance = ev.get("relevance", {})
    completeness = ev.get("completeness", {})
    pico = ev.get("pico", {})
    grade = ev.get("evidence_grade", {})
    reporting = ev.get("reporting_quality", {})
    deep_summary = str(row.get("deep_summary") or "")
    lines: list[str] = [f"EVALUATION — #{row.get('id')} {row.get('title')}", ""]
    # When a deep search ran, lead with the AI's own critical appraisal/verdict —
    # the tab is literally labelled CRITICAL APPRAISAL — then the heuristic signals.
    if deep_summary:
        lines.append("AI CRITICAL APPRAISAL (MiniMax)")
        for para in deep_summary.splitlines() or [""]:
            lines.extend(textwrap.wrap(para, width=max(30, width - 2)) or [""])
        lines.append("")
        lines.append("HEURISTIC SIGNALS")
    lines += [
        "METHODOLOGY",
        f"- Study design: {method.get('study_design', 'unclear')}",
        f"- Blinding: {method.get('blinding', 'not_reported')}",
        f"- Control: {method.get('control', 'not_reported')}",
        f"- Randomization: {method.get('randomization', 'not_reported')}",
        f"- Allocation concealment: {method.get('allocation_concealment', 'not_reported')}",
        f"- Sample size: {method.get('sample_size') or 'not detected'}",
        "",
        "RELEVANCE",
        f"- Pertinence score: {relevance.get('pertinence_score', 0)}",
        f"- Quality score: {ev.get('quality_score', 0)}",
        f"- Clinical outcomes: {', '.join(relevance.get('clinical_outcomes', []) or []) or 'not detected'}",
        "",
        "PICO / COMPLETENESS",
        f"- Completeness: {completeness.get('score', 0)}% | Missing: {', '.join(completeness.get('missing_fields', []) or []) or 'none'}",
        f"- Population: {', '.join(pico.get('study_population', []) or []) or 'not detected'}",
        f"- Intervention: {', '.join(pico.get('intervention_signals', []) or []) or 'not detected'}",
        f"- Comparator: {', '.join(pico.get('comparator_signals', []) or []) or 'not detected'}",
        f"- GRADE: {grade.get('level', 'unclear')} | Downgrades: {', '.join(grade.get('downgrade_reasons', []) or []) or 'none'}",
        f"- Reporting frameworks: {', '.join(reporting.get('frameworks_considered', []) or []) or '—'}",
        "",
        "STATISTICS / BIAS",
        f"- Effect size reported: {ev.get('statistics', {}).get('effect_size_reported', False)}",
        f"- Confidence interval reported: {ev.get('statistics', {}).get('confidence_interval_reported', False)}",
        f"- Bias risk: {ev.get('bias', {}).get('overall_risk', 'unclear')}",
        f"- Bias domains: {', '.join(ev.get('bias', {}).get('domains', []) or []) or 'none detected'}",
        "",
        "FUNDING / CONFLICTS",
        f"- Funding: {', '.join(funding.get('suspected_funders', []) or []) or ('detected' if funding.get('funding_detected') else 'not reported')}",
        f"- Sponsor involvement: {funding.get('sponsor_involvement', False)}",
        f"- Conflict risk: {conflicts.get('conflict_risk_score', 0)}",
        f"- Conflict signals: {', '.join(conflicts.get('signals', []) or []) or 'none'}",
        "",
        "OUTCOMES / INTERPRETATION",
        f"- Primary outcomes: {', '.join(ev.get('outcomes', {}).get('primary_outcomes', []) or []) or 'not detected'}",
        f"- Endpoint type: {ev.get('outcomes', {}).get('endpoint_type', 'unclear')}",
        f"- Interpretation: {ev.get('interpretation', 'insufficient information')}",
        "",
        f"RED FLAGS: {', '.join(ev.get('red_flags', []) or []) or '—'}",
    ]
    wrapped = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=max(30, width - 2)) or [""])
    return "\n".join(wrapped)


def meta_list_line(meta: dict, selected: bool, width: int) -> str:
    """One row in the Meta-Analyses list: marker, date, #studies, query."""
    marker = "▶" if selected else " "
    date = str(meta.get("created_at") or "")[:10] or "—"
    n = int(meta.get("n_studies") or 0)
    query = " ".join(str(meta.get("query") or "Untitled").split())
    line = f"{marker} {date}  {n:>2} std  {query}"
    return line[: max(10, width)]


def clamp_scroll(scroll: int, total_lines: int) -> int:
    """Keep a document scroll offset in range so the last line stays visible.

    Shared by the meta-analysis reader and study reading mode.
    """
    return max(0, min(int(scroll), max(0, int(total_lines) - 1)))


def render_meta_document_text(state: TuiState, width: int = 100) -> str:
    """The open meta-analysis document, wrapped to ``width``.

    Scrolling is applied by the caller (it slices from ``state.meta_scroll``).
    Markdown headers (``#``) are left intact so the draw loop can bold them.
    """
    metas = state.meta_analyses
    if not metas:
        return ("No meta-analysis yet. Press M to compile one from a topic — the "
                "full narrative document then appears here, and is also saved as a "
                ".md file you can reopen later.")
    idx = clamp_index(state.selected_meta, len(metas))
    meta = metas[idx]
    document = state.meta_document or str(meta.get("document_md") or "")
    if not document.strip():
        return "This meta-analysis has no document (the run may have produced no text)."
    header_src = [
        f"META-ANALYSIS · {str(meta.get('created_at') or '')[:16]} · "
        f"{int(meta.get('n_studies') or 0)} studies · {meta.get('lang') or 'fr'}",
        f"file: {meta.get('md_path') or '—'}",
        "",
    ]
    # Wrap *every* line (header included) so nothing — e.g. a long absolute .md
    # path — overruns the detail box's right border. textwrap breaks long words,
    # so a path with no spaces still folds instead of overflowing.
    lines: list[str] = []
    for para in header_src + document.splitlines():
        lines.extend(textwrap.wrap(para, width=max(30, width - 2)) or [""])
    return "\n".join(lines)


def render_demo(db_path: str | Path = DEFAULT_DB_PATH, width: int = 120, theme: str = "bloomberg") -> str:
    state = load_state(db_path)
    title = "DR_NEWPAPER RESEARCH DESK"
    current_lines = []
    for idx, row in enumerate(state.current_articles[:10], 1):
        current_lines.append(f"{idx:02d} {row.get('label') or 'WATCH':<5} {row.get('final_score') or 0:>3} {str(row.get('title') or '')[:70]}")
    saved_lines = []
    for idx, row in enumerate(state.saved_articles[:10], 1):
        saved_lines.append(f"{idx:02d} {row.get('label') or 'WATCH':<5} {row.get('final_score') or 0:>3} {str(row.get('title') or '')[:70]}")
    watch_lines = []
    for row in state.watchlists[:10]:
        try:
            sources = ",".join(json.loads(row.get("sources_json") or "[]"))
        except Exception:
            sources = ""
        watch_lines.append(f"#{row.get('id')} {row.get('name')} :: {row.get('query')} [{sources}]")
    bubble_row = selected_article(state) or (state.current_articles or state.saved_articles or [None])[0]
    bubble = companion_bubble_lines(theme, bubble_row, width=34)
    return "\n".join(render_logo_block(theme, width=width) + [
        "",
        title,
        "=" * min(width, len(title)),
        "",
        f"BUBBLE [{theme_config(theme).get('mascot_name', '—')}]  " + (" / ".join(bubble) or "(no study selected)"),
        f"COMPANION  {companion_line(state)}",
        "",
        render_tab_bar("current", "articles", width=width, theme=theme),
        "",
        "CURRENT RESULTS",
        "---------------",
        "\n".join(current_lines) or "Aucun résultat courant.",
        "",
        "SAVED ARTICLES",
        "--------------",
        "\n".join(saved_lines) or "Aucun article conservé.",
        "",
        "WATCHLISTS",
        "----------",
        "\n".join(watch_lines) or "Aucune watchlist.",
        "",
        "WATCHLIST DETAIL",
        "----------------",
        render_watchlist_text(state, width=width),
        "",
        "EVALUATION",
        "----------",
        render_evaluation_text(state, width=width),
        "",
        "DETAIL",
        "------",
        render_detail_text(state, width=width),
    ])


def _safe_addstr(win, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        win.addstr(y, x, text[: max(0, w - x - 1)], attr)
    except curses.error:
        pass


def _style_for_label(label: str) -> int:
    label = (label or "").upper()
    if label == "HOT":
        return curses.color_pair(2) | curses.A_BOLD
    if label == "RISK":
        return curses.color_pair(5) | curses.A_BOLD
    if label == "NEW":
        return curses.color_pair(3) | curses.A_BOLD
    return curses.color_pair(1)


def _draw_box(win, y: int, x: int, h: int, w: int, title: str, active: bool = False, border: dict | None = None) -> None:
    # Active box = bold theme primary; inactive = dim themed muted (was a flat blue
    # for every theme, which washed out the theming). `border` lets the theme pick
    # its own frame charset so the whole UI — not just the front — changes with the DA.
    bs = border or BORDER_STYLES["round"]
    attr = (curses.color_pair(2) | curses.A_BOLD) if active else (curses.color_pair(6) | curses.A_DIM)
    try:
        for i in range(w):
            _safe_addstr(win, y, x + i, bs["h"], attr)
            _safe_addstr(win, y + h - 1, x + i, bs["h"], attr)
        for j in range(h):
            _safe_addstr(win, y + j, x, bs["v"], attr)
            _safe_addstr(win, y + j, x + w - 1, bs["v"], attr)
        _safe_addstr(win, y, x, bs["tl"], attr); _safe_addstr(win, y, x + w - 1, bs["tr"], attr)
        _safe_addstr(win, y + h - 1, x, bs["bl"], attr); _safe_addstr(win, y + h - 1, x + w - 1, bs["br"], attr)
        # Reverse-video title on the focused box: an unmistakable focus cue that
        # survives even when A_DIM/A_BOLD render weakly in same-hue themes.
        title_attr = (curses.color_pair(2) | curses.A_BOLD | curses.A_REVERSE) if active else attr
        _safe_addstr(win, y, x + 2, f" {title} ", title_attr)
    except curses.error:
        pass


def _active_rows(state: TuiState) -> list[dict[str, Any]]:
    if state.active_tab == "saved":
        return state.saved_articles
    if state.active_tab == "evaluation":
        return state.current_articles or state.saved_articles
    if state.active_tab == "watchlist":
        return state.watchlist_articles
    if state.active_tab == "meta":
        return []  # the meta tab lists meta-analyses, not article rows
    return state.current_articles


def clamp_index(index: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(index, total - 1))


def visible_window(total: int, selected: int, capacity: int) -> tuple[int, int]:
    """Return [start, end) so the selected row stays inside the visible viewport."""
    if total <= 0 or capacity <= 0:
        return 0, 0
    selected = clamp_index(selected, total)
    capacity = min(capacity, total)
    half = max(0, capacity // 2)
    start = selected - half
    start = max(0, min(start, total - capacity))
    return start, start + capacity


def _sync_articles_view(state: TuiState) -> None:
    state.articles = _active_rows(state)
    state.selected_article = clamp_index(state.selected_article, len(state.articles))


def advance_tab(state: TuiState) -> None:
    """Advance to the next tab on Tab, cycling:

        Current → Saved → Evaluation → Watchlist(themes) → Watchlist(studies)
        → Meta-Analyses → Current

    Watchlist has two focus sub-states (browse themes / browse that theme's
    studies); every other tab keeps ``focus == "articles"``. Extracted from the
    key loop so the cycle is unit-testable.
    """
    if state.focus == "articles":
        if state.active_tab == "current":
            state.active_tab = "saved"
        elif state.active_tab == "saved":
            state.active_tab = "evaluation"
        elif state.active_tab == "evaluation":
            state.active_tab = "watchlist"
            state.focus = "watchlists"
            load_watchlist_articles(state)
        elif state.active_tab == "watchlist":
            state.active_tab = "meta"
            open_selected_meta(state)
        else:  # meta → back to the start
            state.active_tab = "current"
    else:
        state.focus = "articles"
        if state.active_tab == "watchlist":
            load_watchlist_articles(state)
        else:
            state.active_tab = "current"
    _sync_articles_view(state)


def begin_prompt(state: TuiState, action: str, label: str, default: str = "") -> None:
    state.mode = "prompt"
    state.prompt_action = action
    state.prompt_label = label
    state.prompt_default = default
    state.prompt_value = ""
    state.status = f"{label}: type, Enter to validate, Esc to cancel"


def cancel_prompt(state: TuiState) -> None:
    state.mode = "normal"
    state.prompt_action = ""
    state.prompt_label = ""
    state.prompt_value = ""
    state.prompt_default = ""
    state.status = "Prompt cancelled — interface ready"


def handle_prompt_key(state: TuiState, key: int) -> tuple[str | None, str | None]:
    """Update prompt state. Returns (action, value) on Enter, (None, None) otherwise."""
    if key in (27,):
        cancel_prompt(state)
        return None, None
    if key in (10, 13, curses.KEY_ENTER):
        value = state.prompt_value.strip() or state.prompt_default
        action = state.prompt_action
        state.mode = "normal"
        state.prompt_action = ""
        state.prompt_label = ""
        state.prompt_value = ""
        state.prompt_default = ""
        return action, value.strip()
    if key in (curses.KEY_BACKSPACE, 127, 8):
        state.prompt_value = state.prompt_value[:-1]
        return None, None
    if 32 <= key <= 126:
        state.prompt_value += chr(key)
    return None, None


def _draw_header(stdscr, state: TuiState, cfg: dict, w: int) -> int:
    spec = _front_spec(state.theme, width=w)
    palette = theme_front_palette(state.theme)
    fp256 = cfg.get("front_palette256") or {}
    bg = _palette_color(palette.get("background"), -1)
    front_roles = ["border", "brand", "emblem", "meta", "controls", "texture"]
    role_pair = {}
    for idx, role in enumerate(front_roles, start=11):
        curses.init_pair(idx, _front_color(palette, fp256, role), bg)
        role_pair[role] = idx
    bold_roles = {"border", "brand", "emblem", "meta", "controls"}
    max_lines = min(len(spec), 16)
    for y, (line, role, emblem) in enumerate(spec[:max_lines]):
        if role == "tagline":
            attr = curses.color_pair(3) | curses.A_BOLD | curses.A_ITALIC if hasattr(curses, "A_ITALIC") else curses.color_pair(3) | curses.A_BOLD
        else:
            attr = curses.color_pair(role_pair.get(role, 16)) | (curses.A_BOLD if role in bold_roles else 0)
        _safe_addstr(stdscr, y, 0, line[:w], attr)
        if emblem:  # paint the companion in its own emblem colour
            ex, etext = emblem
            _safe_addstr(stdscr, y, ex, etext, curses.color_pair(role_pair["emblem"]) | curses.A_BOLD)
    return max_lines + 1


def _companion_anchor(theme: str, w: int) -> tuple[int, int, int]:
    """Screen ``(x, top_row, width)`` of the header companion art.

    Mirrors the placement math in :func:`_front_spec` so the speech bubble can be
    pinned to wherever the companion actually renders (it hugs the right border on
    narrower terminals and settles into a fixed column once there is room).
    """
    cfg = theme_config(theme)
    mascot = cfg.get("mascot", []) or []
    comp_w = max((_disp_width(m) for m in mascot if m.strip()), default=0)
    if not comp_w:
        return w, 1, 0
    inner = max(1, max(72, w) - 2)
    logo_w = max((_disp_width(r) for r in _logo_rows(theme) if r), default=0)
    comp_x = min(2 + logo_w + 3, max(2, inner - comp_w))
    return 1 + comp_x, 1, comp_w


_BUBBLE_MAX_W = 36
_BUBBLE_MIN_W = 20    # below this there is no usable room right of the companion


def _draw_companion_bubble(stdscr, state: TuiState, cfg: dict, w: int) -> None:
    """A speech bubble, anchored just RIGHT of the header companion, showing its POP-UP.

    Event-driven (see update_companion_popups): it appears when a study is
    selected, the user goes idle, or a search finishes/drags on, and clears when
    the pop-up's ≤20s lifetime ends. The companion sits on the right of the header
    (the wordmark/title fills the left), so the bubble lives in the free space to
    the companion's right — never over the title — with a tail pointing back left
    at its face, so the quip reads as coming from the companion. Its width adapts
    to that free space; if there isn't enough (the companion hugs the right border
    on narrower terminals) it is skipped and the always-on header companion_line
    keeps covering commentary."""
    if w < 96:
        return
    text = companion_popup(state)
    if not text:
        return
    emblem_x, comp_top, _comp_w = _companion_anchor(state.theme, w)
    # The mascot art carries trailing padding; anchor to its *visible* right edge so
    # the tail sits right against the face, not floating in the padding.
    mascot = cfg.get("mascot", []) or []
    vis_w = max((_disp_width(m.rstrip()) for m in mascot if m.strip()), default=0)
    x0 = emblem_x + vis_w + 1                       # one-cell gap (the tail) after the face
    bub_w = min(_BUBBLE_MAX_W, (w - 1) - x0)        # fit into the window's right margin
    if bub_w < _BUBBLE_MIN_W:                        # no room right of the companion
        return
    lines = textwrap.wrap(text, bub_w - 4)[:4] or [text[: bub_w - 4]]
    bub_h = len(lines) + 3
    name = cfg.get("mascot_name", "Companion")
    # Clear the box footprint first so the header art behind it doesn't bleed through.
    for j in range(bub_h):
        _safe_addstr(stdscr, j, x0, " " * bub_w, curses.color_pair(6))
    _draw_box(stdscr, 0, x0, bub_h, bub_w, name, True, border=border_style(cfg.get("border_box")))
    text_attr = {"hot": curses.color_pair(2) | curses.A_BOLD,
                 "solid": curses.color_pair(3),
                 "weak": curses.color_pair(5) | curses.A_BOLD,
                 "info": curses.color_pair(4) | curses.A_BOLD,
                 "error": curses.color_pair(5) | curses.A_BOLD}.get(state.popup_band or "solid", curses.color_pair(3))
    for i, ln in enumerate(lines):
        _safe_addstr(stdscr, 1 + i, x0 + 2, ln, text_attr)
    # Speech tail jutting out the bubble's LEFT edge back toward the companion's
    # face, bridging the gap so the bubble visibly belongs to the companion.
    # Anchored to a content row (never the corner) so the box stays closed.
    tail_attr = curses.color_pair(3) | curses.A_BOLD
    tail_row = max(1, min(comp_top + 3, bub_h - 2))                      # ~face level
    _safe_addstr(stdscr, tail_row, x0, "◖", tail_attr)                   # bulge off the side border
    _safe_addstr(stdscr, tail_row, x0 - 1, f"{cfg.get('sparkle', '✳')}", tail_attr)  # tip at the companion


def _draw_prompt_bar(stdscr, state: TuiState, cfg: dict, h: int, w: int) -> None:
    y = h - 2
    _safe_addstr(stdscr, y, 1, " " * max(1, w - 2), curses.color_pair(6))
    if state.mode == "prompt":
        value = state.prompt_value or state.prompt_default
        ghost = "" if state.prompt_value else " (default)"
        text = f"{cfg.get('glyph', '◆')} {state.prompt_label}: {value}{ghost}  ·  Enter validate / Esc cancel"
        _safe_addstr(stdscr, y, 2, text[: max(1, w - 4)], curses.color_pair(2) | curses.A_BOLD)
    elif state.busy:
        verb = state.spinner_verb or "Searching"
        text = format_search_animation(verb, state.spinner_query, state.theme, state.spinner_tick, state.spinner_elapsed)
        _safe_addstr(stdscr, y, 2, text[: max(1, w - 4)], curses.color_pair(2) | curses.A_BOLD)
    else:
        # The companion's voice now lives in the persistent header line
        # (companion_line), so the bottom bar is purely the technical status.
        _safe_addstr(stdscr, y, 2, state.status[: max(1, w - 4)], curses.color_pair(4))


def run_with_spinner(stdscr, state: TuiState, verb: str, work: Callable[[], Any],
                     query: str = "", draw: Callable | None = None,
                     nap: Callable[[int], Any] | None = None, frame_ms: int = 80) -> Any:
    """Run a blocking `work()` in a daemon thread while animating the search spinner.

    The interface stays fully visible; only the bottom status bar animates while
    ``state.busy`` is set. ``_draw`` keeps reading the article/watchlist lists, but
    the worker only reassigns those lists atomically at the very end — never
    mutating them in place — so the worst observable effect is a one-frame refresh,
    not a crash. The worker opens its own ``ResearchStore`` (its own SQLite
    connection), so no connection is shared across threads.
    """
    draw = draw or _draw
    nap = nap or curses.napms
    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["value"] = work()
        except BaseException as exc:  # surface in the UI thread; never die silently
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    state.busy = True
    state.spinner_verb = verb
    state.spinner_query = query
    state.spinner_tick = 0
    state.spinner_elapsed = 0.0
    start = time.monotonic()
    thread.start()
    try:
        with contextlib.suppress(curses.error):
            stdscr.nodelay(True)
        draw(stdscr, state)  # paint frame 0 immediately for instant feedback
        while thread.is_alive():
            state.spinner_tick += 1
            state.spinner_elapsed = time.monotonic() - start
            draw(stdscr, state)
            nap(frame_ms)
            with contextlib.suppress(curses.error):
                while stdscr.getch() != -1:  # drain queued keys so they don't fire later
                    pass
        thread.join()
    finally:
        with contextlib.suppress(curses.error):
            stdscr.nodelay(False)
        state.spinner_elapsed = time.monotonic() - start
        state.busy = False
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _draw_config(stdscr, state: TuiState, tick: int) -> None:
    """Themed, animated configuration screen (Search + Meta tabs)."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    with contextlib.suppress(curses.error):
        curses.curs_set(0)
    cfg = theme_config(state.theme)
    if h < 20 or w < 70:
        _safe_addstr(stdscr, 0, 0, "Terminal too small for config (need 70x20).", curses.color_pair(5) | curses.A_BOLD)
        stdscr.refresh()
        return
    rows = (_config_lines if state.config_tab == "search" else _meta_config_lines)(state, tick)
    box_w = min(w - 2, 86)
    box_h = min(h - 2, len(rows) + 4)
    box_y = max(0, (h - box_h) // 2)
    box_x = max(0, (w - box_w) // 2)
    _draw_box(stdscr, box_y, box_x, box_h, box_w, "CONFIGURATION", True, border=border_style(cfg.get("border_box")))
    # Tab bar: active tab is bracketed and bold; inactive is dim.
    tab_active_attr = curses.color_pair(3) | curses.A_BOLD
    tab_inactive_attr = curses.color_pair(6) | curses.A_DIM
    if state.config_tab == "search":
        _safe_addstr(stdscr, box_y + 1, box_x + 3, "[ Search ]", tab_active_attr)
        _safe_addstr(stdscr, box_y + 1, box_x + 14, "│  Meta-Analysis", tab_inactive_attr)
    else:
        _safe_addstr(stdscr, box_y + 1, box_x + 3, "  Search  ", tab_inactive_attr)
        _safe_addstr(stdscr, box_y + 1, box_x + 14, "│  [ Meta-Analysis ]", tab_active_attr)
    _safe_addstr(stdscr, box_y + 1, box_x + 36, "  ·  Tab ⇆", curses.color_pair(6) | curses.A_DIM)
    kind_attr = {
        "label_on": curses.color_pair(2) | curses.A_BOLD,
        "label_off": curses.color_pair(1),
        "gauge_on": curses.color_pair(2) | curses.A_BOLD,
        "gauge_off": curses.color_pair(6) | curses.A_DIM,
        "note": curses.color_pair(6),
        "preview": curses.color_pair(4) | curses.A_BOLD,
        "help": curses.color_pair(6) | curses.A_DIM,
    }
    y = box_y + 3
    for text, kind, focused in rows:
        if kind in ("label", "seg"):
            attr = kind_attr["label_on"] if focused else kind_attr["label_off"]
        elif kind in ("gauge", "lang_gauge"):
            attr = kind_attr["gauge_on"] if focused else kind_attr["gauge_off"]
        else:
            attr = kind_attr.get(kind, 0)
        _safe_addstr(stdscr, y, box_x + 4, text[: box_w - 6], attr)
        y += 1
        if y >= box_y + box_h - 1:
            break

    # Celebratory shock-wave: search tab uses theme palette, meta tab uses vivid fire colors.
    burst = tick - state.explosion_start_tick
    ex_h, ex_w = box_h - 2 * _CONFIG_BURST_PAD, box_w - 2 * _CONFIG_BURST_PAD
    origin = _config_burst_origin(rows)
    if 0 <= burst < explosion_span(ex_w, ex_h, origin):
        if state.config_tab == "meta":
            tier_attrs = _META_EXPLOSION_TIER_ATTR
            cells_iter = explosion_cells(burst, ex_w, ex_h, state.theme, origin, core="★", ring="✦")
        else:
            tier_attrs = _EXPLOSION_TIER_ATTR
            cells_iter = explosion_cells(burst, ex_w, ex_h, state.theme, origin)
        for ry, cells in enumerate(cells_iter):
            for cx_off, cell in enumerate(cells):
                if cell is None:
                    continue
                glyph, tier = cell
                pair, extra = tier_attrs[tier]
                _safe_addstr(stdscr, box_y + _CONFIG_BURST_PAD + ry,
                             box_x + _CONFIG_BURST_PAD + cx_off, glyph,
                             curses.color_pair(pair) | extra)
    stdscr.refresh()


_ESC_ARROWS = {ord("A"): curses.KEY_UP, ord("B"): curses.KEY_DOWN,
               ord("C"): curses.KEY_RIGHT, ord("D"): curses.KEY_LEFT}

# Keys that stay live while a background search runs: navigation, theme cycling
# and quit. Everything else (new searches, DB mutations) is deferred until the
# worker finishes so it can't race the UI thread over SQLite or shared state.
_BUSY_SAFE_KEYS = frozenset({
    curses.KEY_DOWN, curses.KEY_UP, ord("j"), ord("J"), ord("k"), ord("K"),
    ord("\t"), ord("c"), ord("C"), ord("q"), ord("Q"), 27,
})


def _resolve_escape(stdscr, peek_ms: int = 30, tries: int = 4) -> int:
    """After a bare ESC, peek for an arrow sequence (CSI/SS3) and translate it.

    While a background search runs the input loop reads in non-blocking mode, so
    curses hands us a raw ESC instead of an assembled KEY_DOWN. The continuation
    bytes (``[`` / ``O`` then ``A``-``D``) of the sequence may not have landed in
    the input buffer yet. Peeking with ``timeout(0)`` raced the terminal: a split
    arrow read back as ``-1`` and was mistaken for a lone ESC — which the main
    loop treats as quit, so navigating mid-search killed the worker thread and
    the search silently vanished. Instead we peek with a brief *blocking* wait
    and keep reading across a few slices until the sequence completes; only a
    genuine lone ESC (nothing arrives within the window) still means cancel.
    """
    seq: list[int] = []
    with contextlib.suppress(curses.error):
        stdscr.timeout(peek_ms)  # brief blocking peek so split CSI/SS3 bytes arrive
        for _ in range(tries):
            nxt = stdscr.getch()
            if nxt == -1:
                if len(seq) >= 2:
                    break        # a complete-enough sequence already in hand
                continue          # nothing yet — the ESC bytes may still be in flight
            seq.append(nxt)
            if seq[0] not in (ord("["), ord("O")):
                break             # ESC followed by an ordinary key, not an arrow
            if len(seq) >= 2:
                break             # CSI/SS3 final byte captured
    if len(seq) >= 2 and seq[0] in (ord("["), ord("O")):
        return _ESC_ARROWS.get(seq[-1], 27)
    return 27  # nothing followed → genuine ESC


def run_config_screen(stdscr, state: TuiState, frame_ms: int = 60) -> None:
    """Drive the animated config screen until the user validates/cancels."""
    state.mode = "config"
    tick = 0
    state.explosion_start_tick = -10**9  # no burst pending on (re)entry
    deepest = len(DEPTH_LEVELS) - 1
    meta_deepest = len(META_DEPTH_LEVELS) - 1
    try:
        while True:
            with contextlib.suppress(curses.error):
                stdscr.timeout(frame_ms)  # paces the animation; re-asserted each frame
            _draw_config(stdscr, state, tick)
            key = -1
            with contextlib.suppress(curses.error):
                key = stdscr.getch()
            if key == -1:  # frame timeout — just advance the animation
                tick += 1
                continue
            if key == 27:  # disambiguate bare ESC from an arrow escape sequence
                key = _resolve_escape(stdscr)
            prev_depth = state.depth_idx
            prev_meta_depth = state.meta_depth_idx
            close = config_handle_key(state, key)
            # Detonate when the relevant depth knob crosses INTO its deepest rung.
            # Arm at tick+1 so the next draw's burst == 0 (frame-0 ignition renders).
            if state.depth_idx == deepest and prev_depth != deepest:
                state.explosion_start_tick = tick + 1
            if state.meta_depth_idx == meta_deepest and prev_meta_depth != meta_deepest:
                state.explosion_start_tick = tick + 1
            if close:
                break
            tick += 1
    finally:
        with contextlib.suppress(curses.error):
            stdscr.timeout(-1)  # restore blocking input for the main loop
        state.mode = "normal"
    c = resolve_search_config(state)
    mc = resolve_meta_config(state)
    state.status = (
        f"Search: {c['max']} études · {c['sensitivity']} · {c['depth']} · {c['lang_label']}"
        f"  ·  Meta: {mc['max']} études · {len(mc['sources'])} sources · {mc['analysis_depth']}"
    )


def _draw(stdscr, state: TuiState) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    try:
        curses.curs_set(1 if state.mode == "prompt" and not state.busy else 0)
    except curses.error:
        pass
    cfg = theme_config(state.theme)
    if h < 26 or w < 82:
        _safe_addstr(stdscr, 0, 0, "Terminal too small. Use at least 82x26 for logo mode.", curses.color_pair(5) | curses.A_BOLD)
        stdscr.refresh()
        return

    top = _draw_header(stdscr, state, cfg, w)
    # The companion's always-on voice: one persistent line in the header gap row,
    # recomputed every frame so it visibly reacts to selection / tab / search.
    say = companion_line(state)
    if say:
        say_attr = (curses.color_pair(5) | curses.A_BOLD) if state.notif_kind == "error" and companion_notification(state) else (curses.color_pair(3) | curses.A_BOLD)
        _safe_addstr(stdscr, max(0, top - 1), 2, say[: max(1, w - 4)], say_attr)
    # A playful top-right bubble where the companion quips about the selected study.
    _draw_companion_bubble(stdscr, state, cfg, w)
    # A search that is reporting progress gets a dedicated bordered band at the
    # bottom; reserve rows for it so the article boxes shrink instead of being
    # overlaid — the detail pane stays normal so you can keep browsing and
    # navigating while the search runs.
    _progress_snap = search_progress_snapshot(state)
    searching = _progress_snap is not None
    band_h = progress_band_height(h, top, searching)
    bottom_reserved = 4 + band_h
    work_h = max(MIN_WORK_H, h - top - bottom_reserved)
    left_w = max(46, min(72, w // 2))
    right_w = w - left_w - 3
    watch_h = 5
    article_h = work_h - watch_h - 1

    box_bs = border_style(cfg.get("border_box"))
    # In reading mode the detail pane takes focus (highlighted border), and the
    # list relinquishes it, so it's obvious arrows now scroll the study.
    _draw_box(stdscr, top, 0, article_h, left_w, "ARTICLES", state.focus == "articles" and not state.reading, border=box_bs)
    tab_bar = render_tab_bar(state.active_tab, state.focus, width=left_w - 4, theme=state.theme)
    _safe_addstr(stdscr, top + 1, 2, tab_bar[: max(1, left_w - 4)], curses.color_pair(2) | curses.A_BOLD)
    detail_title = _detail_pane_title(state)
    _draw_box(stdscr, top, left_w + 1, article_h, right_w, detail_title, state.reading, border=box_bs)
    _draw_box(stdscr, top + article_h, 0, watch_h, w - 1, "WATCHLISTS", state.focus == "watchlists", border=box_bs)

    visible_articles = _active_rows(state)
    list_capacity = max(0, article_h - 5)
    article_start = 0
    if state.active_tab == "watchlist" and state.focus == "watchlists":
        visible_articles = []
        w_start, w_end = visible_window(len(state.watchlists), state.selected_watchlist, list_capacity)
        if w_start > 0:
            _safe_addstr(stdscr, top + 2, 2, f"↑ {w_start} more themes"[: left_w - 4], curses.A_DIM)
        for screen_i, row in enumerate(state.watchlists[w_start:w_end]):
            i = w_start + screen_i
            selected = i == state.selected_watchlist
            last = row.get("last_run_at") or "never"
            line = f"{'▶' if selected else ' '} #{row.get('id')} {row.get('name')}  ·  {str(row.get('query') or '')[: max(10, left_w - 24)]}  · {last[:10]}"
            _safe_addstr(stdscr, top + 3 + screen_i, 2, line[: left_w - 4], curses.A_REVERSE if selected else curses.color_pair(1))
        if w_end < len(state.watchlists):
            _safe_addstr(stdscr, top + 3 + (w_end - w_start), 2, f"↓ {len(state.watchlists) - w_end} more themes"[: left_w - 4], curses.A_DIM)
    elif state.active_tab == "meta":
        # The left column lists past meta-analyses; the document reads on the right.
        visible_articles = []
        metas = state.meta_analyses
        if not metas:
            _safe_addstr(stdscr, top + 3, 2, "No meta-analysis yet — press M to compile one."[: left_w - 4], curses.A_DIM)
        else:
            m_start, m_end = visible_window(len(metas), state.selected_meta, list_capacity)
            if m_start > 0:
                _safe_addstr(stdscr, top + 2, 2, f"↑ {m_start} more meta-analyses"[: left_w - 4], curses.A_DIM)
            for screen_i, meta in enumerate(metas[m_start:m_end]):
                i = m_start + screen_i
                selected = i == state.selected_meta and state.focus == "articles"
                line = meta_list_line(meta, selected, left_w - 4)
                _safe_addstr(stdscr, top + 3 + screen_i, 2, line, curses.A_REVERSE if selected else curses.color_pair(1))
            if m_end < len(metas):
                _safe_addstr(stdscr, top + 3 + (m_end - m_start), 2, f"↓ {len(metas) - m_end} more meta-analyses"[: left_w - 4], curses.A_DIM)
    else:
        a_start, a_end = visible_window(len(visible_articles), state.selected_article, list_capacity)
        article_start = a_start
        if a_start > 0:
            _safe_addstr(stdscr, top + 2, 2, f"↑ {a_start} more studies"[: left_w - 4], curses.A_DIM)
        visible_articles = visible_articles[a_start:a_end]
    if not visible_articles and state.active_tab != "meta" and not (state.active_tab == "watchlist" and state.focus == "watchlists"):
        empty = cfg.get("empty", "No results. Press S to search.")
        if state.active_tab == "watchlist":
            empty = "No studies for selected watchlist — Enter refreshes this theme."
        _safe_addstr(stdscr, top + 3, 2, empty[: left_w - 4], curses.A_DIM)
    for screen_i, row in enumerate(visible_articles):
        source_i = article_start + screen_i
        selected = source_i == state.selected_article and state.focus == "articles"
        label = row.get("label") or "WATCH"
        score = int(row.get("final_score") or 0)
        risk = int(row.get("risk_score") or 0)
        year = _parse_year(row) or "----"
        title = str(row.get("title", ""))
        prefix = "▶" if selected else " "
        line = f"{prefix} {label:<5} {score:>3} R{risk:<2} {year}  {title[: max(10, left_w - 25)]}"
        attr = curses.A_REVERSE if selected else _style_for_label(label)
        _safe_addstr(stdscr, top + 3 + screen_i, 2, line, attr)
    if state.focus == "articles":
        total_articles = len(_active_rows(state))
        bottom_remaining = total_articles - (article_start + len(visible_articles))
        if bottom_remaining > 0:
            _safe_addstr(stdscr, top + 3 + len(visible_articles), 2, f"↓ {bottom_remaining} more studies"[: left_w - 4], curses.A_DIM)

    if state.active_tab == "watchlist" and state.focus == "watchlists":
        detail = render_watchlist_text(state, width=max(30, right_w - 4)).splitlines()
    elif state.active_tab == "meta":
        # A long document: slice from the scroll offset so ↑/↓/PgUp/PgDn/Space page
        # it. Clamp here, where the true wrapped length is known, so scroll never
        # runs off the end (and the last line always stays visible).
        full_doc = render_meta_document_text(state, width=max(30, right_w - 4)).splitlines()
        state.meta_scroll = clamp_scroll(state.meta_scroll, len(full_doc))
        detail = full_doc[state.meta_scroll:]
    else:
        detail = (render_evaluation_text(state, width=max(30, right_w - 4)) if state.active_tab == "evaluation" else render_detail_text(state, width=max(30, right_w - 4))).splitlines()
        # Reading mode scrolls the full study detail (summary + abstract) that
        # would otherwise be clipped. detail_scroll is 0 outside reading mode.
        state.detail_scroll = clamp_scroll(state.detail_scroll, len(detail))
        detail = detail[state.detail_scroll:]
    for i, line in enumerate(detail[: max(0, article_h - 3)]):
        attr = curses.A_BOLD | curses.color_pair(2) if line.isupper() or line.startswith("#") or line.startswith("EVALUATION") else 0
        _safe_addstr(stdscr, top + 2 + i, left_w + 3, line, attr)

    for i, row in enumerate(state.watchlists[: max(0, watch_h - 3)]):
        attr = curses.A_REVERSE if i == state.selected_watchlist and state.focus == "watchlists" else curses.color_pair(1)
        try:
            sources = ",".join(json.loads(row.get("sources_json") or "[]"))
        except Exception:
            sources = ""
        _safe_addstr(stdscr, top + article_h + 2 + i, 2, f"#{row.get('id')}  {row.get('name')}  ·  {row.get('query')}  [{sources}]"[: w - 4], attr)

    # The search-progress band: a thin bordered strip just above the prompt bar.
    # Gated on band_h (the reserved rows), so on a too-short terminal it's
    # suppressed — never overlaying the boxes — and the bottom spinner carries on.
    if band_h:
        is_meta = bool(state.task is not None and getattr(state.task, "kind", "") == "meta")
        band_stages = META_STAGES if is_meta else PROGRESS_STAGES
        band_title = "META-ANALYSIS" if is_meta else "SEARCH PROGRESS"
        band_top = h - 2 - band_h
        _draw_box(stdscr, band_top, 0, band_h, w - 1, band_title, True, border=box_bs)
        blines = format_progress_band(_progress_snap, state.theme, state.spinner_tick,
                                      state.spinner_elapsed, width=w - 4, stages=band_stages)
        for i, line in enumerate(blines[: band_h - 2]):
            attr = curses.color_pair(2) | curses.A_BOLD if i == 0 else curses.color_pair(2)
            _safe_addstr(stdscr, band_top + 1 + i, 2, line[: w - 4], attr)
    _draw_prompt_bar(stdscr, state, cfg, h, w)
    if state.reading:
        footer = " Reading study · ↑/↓ & PgUp/PgDn/Space scroll the full summary · Esc / Enter to exit "
    elif state.active_tab == "meta":
        footer = " Meta-Analyses · ↑/↓ & PgUp/PgDn/Space scroll the document · [ / ] previous/next run · M compiles a new one "
    elif state.focus == "watchlists":
        footer = " Topics: t new · e edit subject · X delete topic · Enter run · ↑/↓ select · w/W add/remove study "
    else:
        footer = " Tab: Current→Saved→Evaluation→Watchlist→Meta · Enter reads the study · w/W watchlist · X deletes saved · Z clears "
    _safe_addstr(stdscr, h - 1, 2, footer[: w - 4], curses.A_DIM)
    stdscr.refresh()


def run_curses(db_path: str | Path = DEFAULT_DB_PATH, theme: str = "bloomberg") -> None:
    state = load_state(db_path)
    state.theme = theme_config(theme)["name"]
    _sync_articles_view(state)

    def _perform_prompt_action(stdscr, action: str, value: str) -> None:
        if not value:
            state.status = "Empty prompt ignored"
            return
        # Searches run off the UI thread now (see start_background_task): the
        # interface stays interactive and the companion announces completion.
        if state.busy and action in ("search", "recent_domain", "meta_analysis"):
            return
        if action == "search":
            sc = resolve_search_config(state)
            start_background_task(state, make_search_task(
                state, value, verb=pick_research_verb(), max_results=sc["max"],
                sources=sc["sources"], deep=sc["deep"], allow_scihub=sc["allow_scihub"],
                lang=sc["lang"]))
        elif action == "recent_domain":
            sc = resolve_search_config(state)
            start_background_task(state, make_search_task(
                state, value, verb=pick_research_verb(), max_results=sc["max"],
                sources=sc["sources"], deep=sc["deep"], allow_scihub=sc["allow_scihub"],
                lang=sc["lang"], kind="recent", recent=True))
        elif action == "meta_analysis":
            mc = resolve_meta_config(state)
            # Language is a global knob (Search tab) shared across search & meta.
            start_background_task(state, make_search_task(
                state, value, verb="Meta-analyzing", max_results=mc["max"],
                meta_sources=mc["sources"], lang=resolve_search_config(state)["lang"],
                kind="meta", meta=True, meta_depth=mc["analysis_depth"]))
        elif action == "topic_subject":
            topic_subject_entered(state, value)
        elif action == "topic_name":
            try:
                topic_name_entered(state, value)
            except Exception as exc:
                state.status = f"Follow topic failed: {exc}"
            _sync_articles_view(state)
        elif action == "edit_topic_name":
            state.prompt_default = value  # new name carried into the next step
            store = ResearchStore(state.db_path)
            try:
                row = store.conn.execute(
                    "SELECT query FROM watchlists WHERE id=?", (int(state.editing_watchlist_id or 0),)
                ).fetchone()
            finally:
                store.close()
            begin_prompt(state, "edit_topic_query", "New search to track",
                         (row["query"] if row else "") or state.last_query)
        elif action == "edit_topic_query":
            apply_topic_edit(state, state.prompt_default or "topic", value)

    def _main(stdscr):
        curses.start_color()
        curses.use_default_colors()
        cfg = theme_config(state.theme)
        _apply_theme_pairs(cfg)
        stdscr.keypad(True)
        FRAME_MS = 80
        IDLE_POLL_MS = 500  # coarse wake while merely watching for the idle nudge
        note_interaction(state)                               # start the idle clock
        state.popup_selection_id = _selected_study_id(state)  # no spurious startup pop
        while True:
            now = time.monotonic()
            update_companion_popups(state, now)               # raise event pop-ups
            _draw(stdscr, state)
            # Animate while busy, while a pop-up counts down, or while a toast is
            # live; poll coarsely while only watching for the idle nudge; else block.
            popup_live = companion_popup(state, now) is not None
            animating = state.busy or popup_live or companion_notification(state) is not None
            if animating:
                to = FRAME_MS
            elif not state.idle_popped:
                to = IDLE_POLL_MS
            else:
                to = -1
            with contextlib.suppress(curses.error):
                stdscr.timeout(to)
            key = stdscr.getch()
            if state.busy:
                state.spinner_tick += 1
                if poll_background_task(state):
                    _sync_articles_view(state)
                    # A completed search/meta replaced the list (and possibly the
                    # tab) under us — leave reading mode rather than scroll a study
                    # that may no longer be the selected one.
                    if state.reading:
                        exit_reading(state)
            if key == -1:
                continue  # frame timeout — re-render the animation and re-poll
            note_interaction(state)  # any keypress resets the idle clock
            if key == 27:  # disambiguate a bare ESC from an arrow escape sequence
                key = _resolve_escape(stdscr)
            if state.mode == "prompt":
                action, value = handle_prompt_key(state, key)
                if action is not None:
                    _perform_prompt_action(stdscr, action, value or "")
                continue
            # Reading a study's full detail is modal: every key scrolls or exits,
            # so it must be handled before quit (Esc backs out, not quits) and
            # before the busy gate (reading is read-only and safe mid-search).
            if state.reading:
                reading_handle_key(state, key)
                continue
            # Keep navigation/theme/quit responsive mid-search, but refuse keys
            # that would launch more work or mutate the DB under the worker.
            if state.busy and key not in _BUSY_SAFE_KEYS:
                continue
            if key in (ord("q"), ord("Q"), 27):
                break
            elif key in (ord("\t"),):
                advance_tab(state)
            elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
                if state.active_tab == "meta":
                    state.meta_scroll += 1  # scroll the document (clamped in _draw)
                elif state.focus == "articles":
                    state.selected_article = clamp_index(state.selected_article + 1, len(_active_rows(state)))
                else:
                    state.selected_watchlist = clamp_index(state.selected_watchlist + 1, len(state.watchlists))
                    if state.active_tab == "watchlist":
                        load_watchlist_articles(state)
            elif key in (curses.KEY_UP, ord("k"), ord("K")):
                if state.active_tab == "meta":
                    state.meta_scroll = max(0, state.meta_scroll - 1)
                elif state.focus == "articles":
                    state.selected_article = max(0, state.selected_article - 1)
                else:
                    state.selected_watchlist = clamp_index(state.selected_watchlist - 1, len(state.watchlists))
                    if state.active_tab == "watchlist":
                        load_watchlist_articles(state)
            elif key in (curses.KEY_NPAGE, ord(" ")):
                if state.active_tab == "meta":
                    state.meta_scroll += PAGE_LINES  # clamped in _draw
            elif key == curses.KEY_PPAGE:
                if state.active_tab == "meta":
                    state.meta_scroll = max(0, state.meta_scroll - PAGE_LINES)
            elif key == ord("]"):
                if state.active_tab == "meta" and state.meta_analyses:
                    state.selected_meta = clamp_index(state.selected_meta + 1, len(state.meta_analyses))
                    open_selected_meta(state)
            elif key == ord("["):
                if state.active_tab == "meta" and state.meta_analyses:
                    state.selected_meta = clamp_index(state.selected_meta - 1, len(state.meta_analyses))
                    open_selected_meta(state)
            elif key in (10, 13, curses.KEY_ENTER):
                if state.active_tab == "watchlist" and state.focus == "watchlists":
                    watch = selected_watchlist(state)
                    if watch:
                        start_background_task(state, make_search_task(
                            state, str(watch.get("query") or ""), verb=pick_research_verb(),
                            max_results=8, sources=json.loads(watch.get("sources_json") or "[]"),
                            lang=watch.get("lang") or "fr", kind="watchlist"))
                elif enter_reading(state):
                    # Opened the selected study for full-detail scrolling.
                    state.status = "Reading study — ↑/↓ scroll · Esc to exit"
            elif key in (ord("r"), ord("R")):
                active, focus = state.active_tab, state.focus
                new = load_state(state.db_path)
                state.current_articles = new.current_articles
                state.saved_articles = new.saved_articles
                state.watchlists = new.watchlists
                state.meta_analyses = new.meta_analyses
                state.selected_meta = clamp_index(state.selected_meta, len(state.meta_analyses))
                if state.active_tab == "meta":
                    open_selected_meta(state)
                state.active_tab, state.focus = active, focus
                _sync_articles_view(state)
                state.status = "Reloaded database"
            elif key in (ord("a"), ord("A")):
                evaluate_selected_article(state)
                _sync_articles_view(state)
            elif key in (ord("t"), ord("T")):
                begin_topic_prompt(state)
            elif key in (ord("s"), ord("S")):
                begin_prompt(state, "search", "Search articles", state.last_query)
            elif key in (ord("d"), ord("D")):
                begin_prompt(state, "recent_domain", "Recent domain", "dermatology")
            elif key in (ord("m"), ord("M")):
                begin_prompt(state, "meta_analysis", "Meta-analysis query", state.last_query or "clinical safety")
            elif key in (ord("o"), ord("O")):
                run_config_screen(stdscr, state)
            elif key in (ord("p"), ord("P")):
                pdf_title = str((selected_article(state) or {}).get("title", ""))[:46]
                try:
                    run_with_spinner(stdscr, state, "Fetching PDF",
                                     lambda: download_selected_article(state),
                                     query=pdf_title or "PDF")
                except Exception as exc:
                    state.status = f"PDF download failed: {exc}"
            elif key in (ord("x"), ord("X")):
                # On the themes sidebar X removes the highlighted TOPIC; on the
                # saved library it deletes the selected study.
                if state.focus == "watchlists":
                    delete_selected_topic(state)
                else:
                    delete_selected_saved_article(state)
                _sync_articles_view(state)
            elif key in (ord("z"), ord("Z")):
                clear_saved_articles(state)
                _sync_articles_view(state)
            elif key == ord("w"):
                add_selected_to_watchlist(state)
                _sync_articles_view(state)
            elif key == ord("W"):
                remove_selected_from_watchlist(state)
                _sync_articles_view(state)
            elif key == ord("c"):
                cycle_theme(state, direction=1)
                _apply_theme_pairs(theme_config(state.theme))
            elif key == ord("C"):
                cycle_theme(state, direction=-1)
                _apply_theme_pairs(theme_config(state.theme))
            elif key in (ord("e"), ord("E")) and state.focus == "watchlists":
                # On the themes sidebar E edits the highlighted topic — rename
                # and change its subject (the search query it tracks).
                begin_edit_selected_topic(state)
            elif key in (ord("e"), ord("E")):
                out = Path("Dossier") / "tui_export"
                rows_snapshot = _active_rows(state)[:50]  # snapshot before the worker runs
                try:
                    written = run_with_spinner(
                        stdscr, state, "Compiling dossier",
                        lambda: research_exports.export_research_dossier(out, "TUI export", rows_snapshot, "all"),
                        query="export",
                    )
                    state.status = f"Exported dossier: {written['report']}"
                except Exception as exc:
                    state.status = f"Export failed: {exc}"

    curses.wrapper(_main)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interactive Dr_NewPaper terminal UI")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--theme", choices=sorted(THEMES), default="bloomberg",
                        help="TUI art direction: bloomberg, matrix, cute, synthwave, sepia, ocean")
    parser.add_argument("--demo", action="store_true", help="Render a non-interactive preview and exit")
    parser.add_argument("--spin-demo", action="store_true", help="Print consecutive search-animation frames and exit")
    parser.add_argument("--config-demo", action="store_true", help="Print consecutive config-screen frames and exit")
    parser.add_argument("--progress-demo", action="store_true", help="Print the staged deep-research progress panel frames and exit")
    parser.add_argument("--explosion-demo", action="store_true", help="Print the deepest-depth explosion animation frames and exit")
    args = parser.parse_args(argv)
    if args.spin_demo:
        print(render_spin_demo(theme=args.theme))
        return 0
    if args.explosion_demo:
        print(render_explosion_demo(theme=args.theme))
        return 0
    if args.config_demo:
        print(render_config_demo(theme=args.theme))
        return 0
    if args.progress_demo:
        print(render_progress_demo(theme=args.theme))
        return 0
    if args.demo:
        text = render_demo(args.db, theme=args.theme)
        print(f"Theme: {theme_config(args.theme)['name']} ({theme_config(args.theme)['accent_name']})")
        print(text)
        return 0
    run_curses(args.db, theme=args.theme)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
