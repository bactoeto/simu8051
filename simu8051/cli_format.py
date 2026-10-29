#!/usr/bin/env python3
"""Presentation helpers for the 8051 CLI.

Everything here turns a Session or formatted fields into text. There is no
printing, no argument parsing and no CLI state.
"""
from __future__ import annotations

import os
import sys

from .session import Session

COMMON_SFRS = [
    "P0", "P1", "P2", "P3", "SP", "DPL", "DPH", "PSW", "ACC", "B",
    "IE", "IP", "TCON", "TMOD", "TH0", "TL0", "TH1", "TL1", "SCON", "SBUF", "PCON",
]

RED = "\033[91m"
RESET = "\033[0m"

REG_HEADERS = [
    ["PC", "ACC", "B", "PSW", "SP", "DPTR"],
    [f"R{i}" for i in range(8)],
    ["CY", "AC", "F0", "RS1", "RS0", "OV", "F1", "P"],
    ["T0", "T1", "TMOD", "TCON", "cycle"],
]


def use_color() -> bool:
    """Colour only on a terminal, and never when NO_COLOR is present."""
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text: str, changed: bool, colour: bool | None = None) -> str:
    """Wrap changed text in red, leaving the width of the plain text intact."""
    if not changed:
        return text
    if colour is None:
        colour = use_color()
    return f"{RED}{text}{RESET}" if colour else text


def _table(
    headers: list[list[str]],
    values: list[list[str]],
    previous: list[list[str]] | None = None,
    colour: bool | None = None,
) -> str:
    """Render stacked header/value strips as one box-drawing table."""
    cells = [row for pair in zip(headers, values) for row in pair]
    columns = max(len(row) for row in cells)
    widths = [
        max((len(row[c]) for row in cells if c < len(row)), default=0)
        for c in range(columns)
    ]

    def line(row: list[str], old: list[str] | None) -> str:
        padded = []
        for c in range(columns):
            text = row[c] if c < len(row) else ""
            changed = old is not None and c < len(old) and old[c] != text
            field = paint(text, changed, colour)
            padded.append(f" {field}{' ' * (widths[c] - len(text))} ")
        return "│" + "│".join(padded) + "│"

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (width + 2) for width in widths) + right

    lines = [rule("┌", "┬", "┐")]
    for index, (header, value) in enumerate(zip(headers, values)):
        old = previous[index] if previous is not None and index < len(previous) else None
        lines += [line(header, None), line(value, old)]
        lines.append(
            rule("└", "┴", "┘")
            if index == len(headers) - 1
            else rule("├", "┼", "┤")
        )
    return "\n".join(lines)


def reg_values(session: Session) -> list[list[str]]:
    psw = session.get_sfr("PSW")
    t0 = (session.get_sfr("TH0") << 8) | session.get_sfr("TL0")
    t1 = (session.get_sfr("TH1") << 8) | session.get_sfr("TL1")
    return [
        [
            f"0x{session.pc:04X}",
            f"0x{session.get_reg('A'):02X}",
            f"0x{session.get_reg('B'):02X}",
            f"0x{psw:02X}",
            f"0x{session.get_sfr('SP'):02X}",
            f"0x{session.get_reg('DPTR'):04X}",
        ],
        [f"0x{session.get_reg(f'R{i}'):02X}" for i in range(8)],
        [str((psw >> bit) & 1) for bit in range(7, -1, -1)],
        [
            f"0x{t0:04X}",
            f"0x{t1:04X}",
            f"0x{session.get_sfr('TMOD'):02X}",
            f"0x{session.get_sfr('TCON'):02X}",
            str(session.cycle),
        ],
    ]


def render_lines(
    lines: list[list[str]],
    previous: list[list[str]] | None = None,
    colour: bool | None = None,
    separator: str = " ",
) -> str:
    """Join fields into lines, colouring fields that differ from the snapshot."""
    out = []
    for row, fields in enumerate(lines):
        old = previous[row] if previous is not None and row < len(previous) else None
        parts = []
        for column, text in enumerate(fields):
            changed = old is not None and column < len(old) and old[column] != text
            parts.append(paint(text, changed, colour))
        out.append(separator.join(parts))
    return "\n".join(out)


def format_sfr(session: Session, name: str) -> str:
    try:
        value = session.get_sfr(name)
    except KeyError:
        return f"{name}=<unknown>"
    return f"{name}=0x{value:02X}"


def sfr_lines(session: Session, names: list[str]) -> list[list[str]]:
    return [[format_sfr(session, name) for name in names]]


def mem_lines(
    session: Session,
    space: str,
    address: int,
    count: int | None = None,
) -> list[list[str]]:
    start = address & ~7
    if count is None:
        stop = start + 16
    else:
        stop = max((address + count + 7) & ~7, start + 8)
    digits = 4 if space in {"x", "c"} else 2
    mask = 0xFFFF if digits == 4 else 0xFF
    lines = []
    for row_start in range(start, stop, 8):
        row = [f"0x{row_start & mask:0{digits}X}:"]
        row += [
            f"{_read_space(session, space, (row_start + i) & mask):02X}"
            for i in range(8)
        ]
        lines.append(row)
    return lines


def disassembly_lines(session: Session, address: int, count: int) -> list[list[str]]:
    return [
        [f"{inst.address:04X}  {inst.raw.hex():<6}   {session.format(inst)}"]
        for inst in session.disassemble(address, count)
    ]


def format_mem(
    session: Session,
    space: str,
    address: int,
    count: int | None = None,
) -> str:
    return render_lines(mem_lines(session, space, address, count))


def _read_space(session: Session, space: str, address: int) -> int:
    if space == "c":
        return session.read("code", address)
    if space == "i":
        return session.read("iram", address)
    if space == "x":
        return session.read("xdata", address)
    if space == "bit":
        return session.read("bit", address)
    raise ValueError(f"unknown space {space!r}")
