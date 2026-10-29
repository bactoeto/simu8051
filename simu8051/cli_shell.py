#!/usr/bin/env python3
"""Readline, history and completion helpers for the 8051 CLI."""
from __future__ import annotations

import atexit
import glob
import os
import sys
from pathlib import Path

try:
    import readline as _readline
except ImportError:  # Windows without pyreadline3
    _readline = None

from .session import Session

COMMANDS = (
    "run", "step", "cycle", "continue", "break", "delete",
    "regs", "sfr", "x", "w", "disassemble", "reset", "load",
    "display", "undisplay", "enable", "disable", "info", "help", "quit",
)
ALIASES = {
    "r": "run",
    "s": "step",
    "cy": "cycle",
    "c": "continue",
    "b": "break",
    "del": "delete",
    "clear": "delete",
    "d": "disassemble",
    "i": "info",
    "reg": "regs",
    "sf": "sfr",
    "res": "reset",
    "l": "load",
    "dp": "display",
    "und": "undisplay",
    "en": "enable",
    "on": "enable",
    "off": "disable",
    "h": "help",
    "?": "help",
    "q": "quit",
    "exit": "quit",
}
DISPLAY_COMMANDS = ("sfr", "x", "disassemble")
SPACES = ("i", "x", "c")
INFO_KINDS = ("break", "display")
SFR_NAMES = Session.sfr_names


def canonical(word: str) -> str:
    """Resolve a command or subcommand alias to its canonical name."""
    return ALIASES.get(word.lower(), word.lower())


def history_path() -> Path:
    """Return the readline history file, honouring XDG_STATE_HOME."""
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "simu8051" / "history"


def path_candidates(text: str) -> list[str]:
    """Complete file system paths for the `load` command."""
    matches = []
    for found in glob.glob(os.path.expanduser(text) + "*"):
        matches.append(found + os.sep if os.path.isdir(found) else found)
    return sorted(matches)


def completion_candidates(tokens: list[str], text: str) -> list[str]:
    """Return possible completions for the word after `tokens`."""
    if not tokens:
        return [name for name in (*COMMANDS, *ALIASES) if name.startswith(text)]
    head = canonical(tokens[0])
    if head == "load" and len(tokens) == 1:
        return path_candidates(text)
    if head == "sfr" and len(tokens) == 1:
        return [name for name in SFR_NAMES if name.lower().startswith(text.lower())]
    if head in {"x", "w"} and len(tokens) == 1:
        return [name for name in SPACES if name.startswith(text.lower())]
    if head == "display":
        if len(tokens) == 1:
            return [name for name in DISPLAY_COMMANDS if name.startswith(text.lower())]
        return completion_candidates(tokens[1:], text)
    if head == "break" and len(tokens) == 1:
        return [name for name in ("cycle", "cy") if name.startswith(text.lower())]
    if head == "info" and len(tokens) == 1:
        return [name for name in (*INFO_KINDS, "b", "dp") if name.startswith(text.lower())]
    if head == "reset" and len(tokens) == 1:
        return [name for name in ("cold", "c") if name.startswith(text.lower())]
    return []


def _completer(text: str, state: int) -> str | None:
    if _readline is None:
        return None
    line = _readline.get_line_buffer()
    tokens = line[: _readline.get_begidx()].split()
    matches = completion_candidates(tokens, text)
    return matches[state] if state < len(matches) else None


_history_installed = False


def install_readline() -> None:
    """Enable history and completion when stdin is a terminal."""
    global _history_installed
    if _readline is None or _history_installed or not sys.stdin.isatty():
        return
    _history_installed = True
    path = history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _readline.read_history_file(str(path))
    except OSError:
        pass
    _readline.set_history_length(1000)
    _readline.set_completer(_completer)
    _readline.parse_and_bind("tab: complete")
    atexit.register(_save_history)


def _save_history() -> None:
    if _readline is None:
        return
    try:
        _readline.write_history_file(str(history_path()))
    except OSError:
        pass


def prompt_text() -> str:
    """Return the interactive prompt, coloured only on a terminal."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None:
        return "8051> "
    colour = "\033[32m8051>\033[0m "
    if _readline is None:
        return colour
    return "\001\033[32m\0028051>\001\033[0m\002 "
