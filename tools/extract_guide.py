#!/usr/bin/env python3
"""Extract instruction definitions and memory tables from the non-scanned
MCS-51 Programmer's Guide (build/guide.txt).

Output: build/guide_facts.json

The guide is the authoritative source for detailed instruction semantics,
memory organisation and reset values. This script keeps page/line references
so every extracted fact can be traced back.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TXT = ROOT / "build" / "guide.txt"
DEFAULT_PDF = ROOT / "docs" / "MCS-51_Programmer’s Guide_and_Instruction_Set.PDF"

HEADING_RE = re.compile(r"^\*?([A-Z]{2,6})(?:\s{2,}(\S.*?))?\s*$")
FIELD_RE = {
    "function": re.compile(r"^\s*Function:\s*(.*)$"),
    "description": re.compile(r"^\s*Description:\s*(.*)$"),
    "example": re.compile(r"^\s*Example:\s*(.*)$"),
    "bytes": re.compile(r"^\s*Bytes:\s*(\d+)\s*$"),
    "cycles": re.compile(r"^\s*Cycles:\s*(\d+)\s*$"),
    "encoding": re.compile(r"^\s*Encoding:\s*(.*)$"),
    "operation": re.compile(r"^\s*Operation:\s*(.*)$"),
}

SKIP_PREFIXES = (
    "MCSÉ",
    "MCS",
    "CONTENTS",
    "INSTRUCTION DEFINITIONS",
    "PROGRAM MEMORY",
    "Data Memory",
    "INDIRECT ADDRESS",
    "DIRECT AND INDIRECT",
    "SPECIAL FUNCTION",
    "WHAT DO THE SFRs",
    "SFR MEMORY MAP",
    "Those SFRs",
    "NOTE",
    "The information",
)


def normalise_syntax(text: str) -> str:
    """Normalise the guide's heading syntax for matching against HTML."""
    text = text.replace("Ý", "#").replace("Ð", "-")
    text = re.sub(r"\bk\s+", "", text)
    text = re.sub(r"\s+l\b", "", text)
    text = re.sub(r"\s*,\s*", ",", text)
    text = re.sub(r"\s*@\s*", "@", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("@ Ri", "@Ri").replace("@ R0", "@R0").replace("@ R1", "@R1")
    return text


def clean_lines(lines: list[str]) -> str:
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"\d+", s):
            continue
        if s.startswith(("MCSÉ-51 PROGRAMMER", "MCS-51 PROGRAMMER")):
            continue
        out.append(s)
    text = " ".join(out)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_blocks(lines: list[str], start_idx: int) -> list[dict]:
    """Split the instruction-definitions region into heading-delimited blocks."""
    headings: list[tuple[int, str, str]] = []
    for i in range(start_idx, len(lines)):
        raw = lines[i]
        if not raw or raw[0].isspace():
            continue
        m = HEADING_RE.match(raw)
        if not m:
            continue
        mnemonic, rest = m.group(1), (m.group(2) or "").strip()
        if mnemonic in {
            "MCSÉ",
            "MCS",
            "CONTENTS",
            "INSTRUCTION",
            "PROGRAM",
            "SPECIAL",
            "WHAT",
            "SFR",
            "NOTE",
        }:
            continue
        if rest.startswith(("PROGRAMMER", "GUIDE")):
            continue
        headings.append((i, mnemonic, rest, raw))

    blocks: list[dict] = []
    for n, (i, mnemonic, rest, raw) in enumerate(headings):
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        body = lines[i + 1 : end]
        fields: dict[str, str] = {"function": "", "description": "", "example": ""}
        current: str | None = None
        buckets: dict[str, list[str]] = {
            k: [] for k in ("function", "description", "example", "encoding", "operation")
        }
        for line in body:
            matched = False
            for key, rx in FIELD_RE.items():
                m = rx.match(line)
                if m:
                    current = key
                    if key in {"bytes", "cycles"}:
                        # Keep the first value; a block should contain one form.
                        fields.setdefault(key, m.group(1))
                    else:
                        buckets[key].append(m.group(1))
                    matched = True
                    break
            if matched:
                continue
            if current in buckets and line.strip():
                buckets[current].append(line)

        for key in ("function", "description", "example"):
            fields[key] = clean_lines(buckets.get(key, []))
        block = {
            "line": i + 1,
            "heading_raw": raw,
            "heading": normalise_syntax(f"{mnemonic} {rest}"),
            "mnemonic": mnemonic,
            "operands": normalise_syntax(rest),
            "footnote": raw.lstrip().startswith("*"),
            "bytes": int(fields["bytes"]) if fields.get("bytes") else None,
            "cycles": int(fields["cycles"]) if fields.get("cycles") else None,
            "function": fields["function"],
            "description": fields["description"],
            "example": fields["example"],
            "encoding": clean_lines(buckets.get("encoding", [])),
            "operation": clean_lines(buckets.get("operation", [])),
        }
        blocks.append(block)
    return blocks


def parse_table11(lines: list[str]) -> list[dict]:
    """Parse Table 11: opcode, number of bytes, mnemonic, operands."""
    rows: list[dict] = []
    in_table = False
    for line in lines:
        if "Table 11" in line and "Hexadecimal Order" in line:
            in_table = True
            continue
        if in_table and "INSTRUCTION DEFINITIONS" in line:
            break
        if not in_table:
            continue
        # The table is laid out in two fixed-width columns (second starts
        # at column 52). Split before parsing so operands cannot swallow
        # the next record.
        for half in (line[:52], line[52:]):
            m = re.match(
                r"\s*([0-9A-F]{2})\s+(\d+)\s+([A-Z]{2,6})(?:\s+(.*?))?\s*$",
                half,
            )
            if not m:
                continue
            opcode, nbytes, mnemonic, operands = m.groups()
            rows.append(
                {
                    "opcode": f"0x{int(opcode, 16):02X}",
                    "bytes": int(nbytes),
                    "mnemonic": mnemonic,
                    "operands": normalise_syntax(operands or ""),
                }
            )
    return rows


def parse_sfr_table(lines: list[str]) -> list[dict]:
    """Parse Table 1 (SFR symbols, names, addresses)."""
    start = None
    for i, line in enumerate(lines):
        if "Table 1" in line and "Symbol" not in line:
            # The heading is on its own line; table follows.
            start = i
            break
    if start is None:
        return []
    rows = []
    for line in lines[start : start + 80]:
        m = re.match(r"\s*([*a ]*)\s*([A-Z][A-Z0-9]*)\s{2,}(.+?)\s{2,}([0-9A-F]{2,3})H\s*$", line)
        if not m:
            continue
        flags, symbol, name, addr = m.groups()
        rows.append(
            {
                "symbol": symbol,
                "name": name.strip(),
                "address": f"0x{addr}",
                "bit_addressable": "*" in flags,
                "notes": flags.strip(),
            }
        )
    return rows


def parse_reset_table(lines: list[str]) -> list[dict]:
    """Parse Table 2 (SFR contents after reset)."""
    start = None
    for i, line in enumerate(lines):
        if "Contents of the SFRs after reset" in line:
            start = i
            break
    if start is None:
        return []
    rows = []
    for line in lines[start : start + 80]:
        m = re.match(
            r"\s*([*a ]*)\s*([A-Z][A-Z0-9]*)\s{2,}([01Xx]+)\s*$", line
        )
        if m:
            flags, symbol, value = m.groups()
            rows.append(
                {
                    "symbol": symbol,
                    "reset_bits": value,
                    "bit_addressable": "*" in flags,
                    "notes": flags.strip(),
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", type=Path, default=DEFAULT_TXT)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "guide_facts.json")
    args = ap.parse_args(argv)

    text = args.txt.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Skip the table of contents; use the second occurrence of the heading.
    starts = [i for i, line in enumerate(lines) if line.strip() == "INSTRUCTION DEFINITIONS"]
    start_idx = starts[-1] if starts else 0
    blocks = split_blocks(lines, start_idx)

    forms = [b for b in blocks if b["bytes"] is not None and b["cycles"] is not None]
    intros = [b for b in blocks if b["bytes"] is None or b["cycles"] is None]

    table11 = parse_table11(lines)
    sfr_table = parse_sfr_table(lines)
    reset_table = parse_reset_table(lines)

    out = {
        "source": str(args.txt.relative_to(ROOT)) if args.txt.is_relative_to(ROOT) else str(args.txt),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "definition_start_line": start_idx + 1,
        "forms": forms,
        "intros": intros,
        "table11": table11,
        "sfr_table": sfr_table,
        "reset_table": reset_table,
        "counts": {
            "blocks": len(blocks),
            "forms": len(forms),
            "intros": len(intros),
            "table11": len(table11),
            "sfr_table": len(sfr_table),
            "reset_table": len(reset_table),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps(out["counts"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
