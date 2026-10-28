#!/usr/bin/env python3
"""Extract opcode/bytes/flags facts from the 8051 instruction-set HTML.

Output: build/html_facts.json

This script only extracts what the HTML actually contains: opcode grid,
mnemonic, syntax, byte count and affected flags. Cycle counts are NOT in
this source and must come from the scanned/guide PDFs.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "docs" / "8051 Instruction Set.html"
DEFAULT_OUT = ROOT / "build" / "html_facts.json"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(fragment: str) -> str:
    """Strip tags, unescape entities, collapse whitespace."""
    text = TAG_RE.sub(" ", fragment)
    text = html.unescape(text)
    return WS_RE.sub(" ", text).strip()


def find_first(pattern: str, text: str, start: int = 0) -> re.Match | None:
    return re.search(pattern, text[start:], re.IGNORECASE | re.DOTALL)


def parse_grid(page: str) -> tuple[list[dict], list[str]]:
    """Parse the 16x16 opcode grid at the top of the HTML."""
    first_anchor = re.search(r"<A\s+NAME=", page, re.IGNORECASE)
    grid_text = page[: first_anchor.start()] if first_anchor else page
    table = re.search(r"<TABLE[^>]*>(.*?)</TABLE>", grid_text, re.IGNORECASE | re.DOTALL)
    if not table:
        raise SystemExit("could not find opcode grid table")

    opcodes: list[dict] = []
    problems: list[str] = []
    for tr in re.split(r"<TR\b", table.group(1), flags=re.IGNORECASE)[1:]:
        cells = re.findall(r"<T[HD][^>]*>(.*?)(?=</T[HD]>|<T[HD]|</TR>|$)", tr, re.IGNORECASE | re.DOTALL)
        if not cells:
            continue
        row_label = clean(cells[0])
        m = re.fullmatch(r"0x([0-9A-Fa-f]{2})", row_label)
        if not m:
            continue
        base = int(m.group(1), 16)
        if base % 16 != 0:
            problems.append(f"grid row label not multiple of 16: {row_label}")
        data_cells = cells[1:]
        if len(data_cells) != 16:
            problems.append(f"grid row {row_label} has {len(data_cells)} cells, expected 16")
        for col, cell in enumerate(data_cells[:16]):
            opcode = base + col
            link = re.search(r'<A\s+HREF="#([^"]+)"[^>]*>(.*?)</A>', cell, re.IGNORECASE | re.DOTALL)
            if link:
                mnemonic = clean(link.group(2))
                anchor = link.group(1)
            else:
                mnemonic = clean(cell) or "reserved"
                anchor = None
            opcodes.append(
                {
                    "opcode": f"0x{opcode:02X}",
                    "mnemonic": mnemonic,
                    "anchor": anchor,
                }
            )

    if len(opcodes) != 256:
        problems.append(f"grid produced {len(opcodes)} opcodes, expected 256")
    return opcodes, problems


def parse_detail_sections(page: str) -> list[dict]:
    """Split at <A NAME=...> anchors and parse each instruction section."""
    anchors = list(re.finditer(r'<A\s+NAME="([^"]+)"', page, re.IGNORECASE))
    sections: list[dict] = []
    for i, match in enumerate(anchors):
        anchor = match.group(1)
        start = match.end()
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(page)
        body = page[start:end]
        # Drop the header of the next section if it leaked in.
        body = re.split(r"<HR\b", body, maxsplit=1, flags=re.IGNORECASE)[0]

        header = {"operation": "", "function": "", "syntax": ""}
        for key, label in (("operation", "Operation"), ("function", "Function"), ("syntax", "Syntax")):
            m = re.search(
                rf"<B>{label}:</TD>\s*<TD[^>]*>(.*?)(?=</TR>|$)",
                body,
                re.IGNORECASE | re.DOTALL,
            )
            if m:
                header[key] = clean(m.group(1))

        rows: list[dict] = []
        # Some sections (e.g. ADD) wrap two side-by-side detail tables in an
        # outer table, so collect every BORDER table in the section.
        for detail_table in re.finditer(
            r"<TABLE[^>]*BORDER[^>]*>(.*?)</TABLE>", body, re.IGNORECASE | re.DOTALL
        ):
            for tr in re.split(r"<TR\b", detail_table.group(1), flags=re.IGNORECASE)[1:]:
                cells = re.findall(
                    r"<TD[^>]*>(.*?)(?=</TD>|<TD|</TR>|$)", tr, re.IGNORECASE | re.DOTALL
                )
                if len(cells) < 4:
                    continue
                syntax = clean(cells[0])
                opcode_text = clean(cells[1])
                bytes_text = clean(cells[2])
                flags_text = clean(cells[3])
                m = re.fullmatch(r"0x([0-9A-Fa-f]{2})", opcode_text)
                if not m:
                    continue
                try:
                    nbytes = int(bytes_text, 0)
                except ValueError:
                    nbytes = None
                rows.append(
                    {
                        "syntax": syntax,
                        "opcode": f"0x{int(m.group(1), 16):02X}",
                        "bytes": nbytes,
                        "flags_text": flags_text,
                        "anchor": anchor,
                    }
                )
        desc_match = re.search(
            r"<B>Description:</B>(.*?)(?=<B>See Also|$)",
            body,
            re.IGNORECASE | re.DOTALL,
        )
        description = clean(desc_match.group(1)) if desc_match else ""

        sections.append(
            {"anchor": anchor, **header, "description": description, "rows": rows}
        )
    return sections


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    raw = args.src.read_bytes()
    page = raw.decode("utf-8", errors="replace")
    opcodes, problems = parse_grid(page)
    sections = parse_detail_sections(page)

    # Cross-check grid vs detail rows.
    detail_by_opcode: dict[str, dict] = {}
    for sec in sections:
        for row in sec["rows"]:
            op = row["opcode"]
            if op in detail_by_opcode:
                problems.append(f"duplicate detail opcode {op}")
            detail_by_opcode[op] = row

    grid_by_opcode = {row["opcode"]: row for row in opcodes}
    for op, row in grid_by_opcode.items():
        if op == "0xA5" or row["mnemonic"].lower() in {"reserved", "?"}:
            continue
        det = detail_by_opcode.get(op)
        if not det:
            problems.append(f"{op} {row['mnemonic']} present in grid but missing in detail tables")
            continue
        detail_mnemonic = det["syntax"].split(" ")[0].lower()
        grid_mnemonic = row["mnemonic"].lower()
        # The grid often links ADDC rows to the ADD section; accept the
        # documented pair as well as an exact match.
        if grid_mnemonic != detail_mnemonic and not (
            grid_mnemonic == "addc" and detail_mnemonic == "addc"
        ):
            problems.append(
                f"{op}: grid mnemonic {row['mnemonic']!r} != detail syntax {det['syntax']!r}"
            )

    for op in detail_by_opcode:
        if op not in grid_by_opcode:
            problems.append(f"{op} present in detail but missing in grid")

    out = {
        "source": str(args.src.relative_to(ROOT)) if args.src.is_relative_to(ROOT) else str(args.src),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "opcodes": opcodes,
        "sections": sections,
        "detail_rows": detail_by_opcode,
        "counts": {
            "grid_opcodes": len(opcodes),
            "sections": len(sections),
            "detail_rows": len(detail_by_opcode),
        },
        "problems": problems,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {args.out}")
    print(json.dumps(out["counts"], indent=2))
    if problems:
        print(f"PROBLEMS: {len(problems)}")
        for p in problems[:50]:
            print(" -", p)
        return 1
    print("no grid/detail problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
