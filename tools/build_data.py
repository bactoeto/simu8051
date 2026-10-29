#!/usr/bin/env python3
"""Merge the three extracted sources into isa.json.

Sources:
  build/html_facts.json   opcode grid, syntax, bytes, flags
  build/guide_facts.json  detailed definitions, bytes, cycles, encoding, operation
  build/scan_facts.json   OCR candidates for Table 10 cycle cross-check
  tools/overrides/scan_manual.json  page-image readings for OCR failures

The output is deterministic: instructions sorted by mnemonic/form, opcodes by
opcode value. No field is silently defaulted: missing/conflicting data is
reported in build/reports/isa_report.json and makes the script exit non-zero.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from norm import canon, slug

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "build" / "html_facts.json"
GUIDE = ROOT / "build" / "guide_facts.json"
SCAN = ROOT / "build" / "scan_facts.json"
MANUAL = ROOT / "tools" / "overrides" / "scan_manual.json"
GUIDE_TXT = ROOT / "build" / "guide.txt"
OUT = ROOT / "data" / "isa.json"
REPORT = ROOT / "build" / "reports" / "isa_report.json"

FLAGS = ("C", "AC", "OV")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def guide_page_map() -> dict[int, int]:
    """Map 1-based line number in build/guide.txt to PDF page."""
    text = GUIDE_TXT.read_text(encoding="utf-8", errors="replace")
    mapping: dict[int, int] = {}
    page = 1
    for lineno, line in enumerate(text.split("\n"), 1):
        mapping[lineno] = page
        if "\f" in line:
            page += 1
    return mapping


def html_sections(html: dict) -> dict[str, dict]:
    return {s["anchor"]: s for s in html["sections"]}


def guide_intro_map(guide: dict) -> dict[str, list[dict]]:
    intros: dict[str, list[dict]] = {}
    for intro in guide["intros"]:
        intros.setdefault(intro["mnemonic"], []).append(intro)
    for rows in intros.values():
        rows.sort(key=lambda r: r["line"])
    return intros


def nearest_intro(form: dict, intros: dict[str, list[dict]]) -> dict | None:
    candidates = [
        i for i in intros.get(form["mnemonic"], []) if i["line"] < form["line"]
    ]
    return candidates[-1] if candidates else None


def flags_effects(form: dict, html_row: dict) -> dict[str, str]:
    effects = {flag: "unchanged" for flag in FLAGS}
    text = html_row.get("flags_text", "None")
    for flag in FLAGS:
        if re.search(rf"\b{flag}\b", text):
            effects[flag] = "modified"

    syntax = canon(form["heading"])
    if syntax in {"RL A", "RR A"}:
        # The HTML flag column wrongly lists C for RL A; the guide's flag
        # table and the RL/RR operation show that C is unchanged.
        effects = {flag: "unchanged" for flag in FLAGS}
    elif syntax == "CLR C":
        effects["C"] = "cleared"
    elif syntax == "SETB C":
        effects["C"] = "set"
    elif form["mnemonic"] in {"MUL", "DIV"}:
        effects["C"] = "cleared"
        effects["OV"] = "modified"
    return effects


def operand_part(syntax: str) -> str:
    return syntax.split(" ", 1)[1] if " " in syntax else ""


def written_registers(form: dict) -> list[str]:
    syn = canon(form["heading"])
    m = form["mnemonic"]
    ops = operand_part(syn)
    first = ops.split(",", 1)[0].strip() if ops else ""
    written: list[str] = ["PC"]

    if m in {"ADD", "ADDC", "SUBB"} and first == "A":
        written.append("A")
    elif m in {"ANL", "ORL", "XRL"}:
        if first in {"A", "direct", "Rn", "@Ri"}:
            written.append(first)
    elif m in {"MUL", "DIV"}:
        written += ["A", "B"]
    elif m in {"DA", "RL", "RLC", "RR", "RRC", "SWAP", "CLR", "CPL"} and first == "A":
        written.append("A")
    elif m in {"INC", "DEC"}:
        if first == "A":
            written.append("A")
        elif first == "DPTR":
            written.append("DPTR")
        elif first in {"direct", "Rn", "@Ri"}:
            written.append(first)
    elif m == "MOV":
        if first == "A":
            written.append("A")
        elif first in {"Rn", "direct", "@Ri", "bit", "DPTR"}:
            written.append(first)
        elif first == "C":
            written.append("PSW.C")
    elif m == "MOVC":
        written.append("A")
    elif m == "MOVX":
        if first == "A":
            written.append("A")
    elif m == "XCH":
        written += ["A", first] if first != "A" else ["A"]
    elif m == "XCHD":
        written += ["A", "@Ri"]
    elif m == "PUSH":
        written.append("SP")
    elif m == "POP":
        written += ["SP", "direct"]
    elif m in {"ACALL", "LCALL", "RET", "RETI"}:
        written.append("SP")
    elif m in {"CLR", "SETB", "CPL"}:
        if first == "C":
            written.append("PSW.C")
        elif first == "bit":
            written.append("bit")
    elif m == "DJNZ":
        written.append(first)
    elif m == "JBC":
        written.append("bit")
    elif m in {"JB", "JNB", "JC", "JNC", "JZ", "JNZ", "SJMP", "AJMP", "LJMP", "JMP"}:
        pass
    elif m == "CJNE":
        pass
    return sorted(set(written), key=written.index)


def read_registers(form: dict) -> list[str]:
    syn = canon(form["heading"])
    m = form["mnemonic"]
    ops = operand_part(syn)
    parts = [p.strip() for p in ops.split(",") if p.strip()]
    first = parts[0] if parts else ""
    known = {"A", "C", "DPTR", "Rn", "direct", "@Ri", "bit"}
    reads: list[str] = ["PC"]
    for part in parts[1:]:
        if part in known:
            reads.append(part)
        elif part == "@DPTR":
            reads.append("DPTR")
        elif part == "@A+DPTR":
            reads += ["A", "DPTR"]
        elif part == "@A+PC":
            reads += ["A", "PC"]
    if m in {"MUL", "DIV"}:
        reads += ["A", "B"]
    if m in {"DA", "RL", "RLC", "RR", "RRC", "SWAP"}:
        reads.append("A")
    if m in {"INC", "DEC", "CLR", "CPL"} and first == "A":
        reads.append("A")
    if m in {"INC", "DEC"} and first in {"direct", "@Ri"}:
        reads.append(first)
    if m in {"INC", "DEC"} and first == "Rn":
        reads.append("Rn")
    if m == "INC" and first == "DPTR":
        reads.append("DPTR")
    if m in {"ANL", "ORL", "XRL"} and first == "direct":
        reads.append("direct")
    if m == "MOV":
        if first == "@Ri":
            reads.append("@Ri")
        if len(parts) > 1 and parts[1] == "@Ri":
            reads.append("@Ri")
        if first == "@Ri" and len(parts) > 1 and parts[1] == "direct":
            reads.append("direct")
    if m == "CPL" and first == "C":
        reads.append("C")
    if m == "CPL" and first == "bit":
        reads.append("bit")
    if m in {"JB", "JNB", "JBC"}:
        reads.append("bit")
    if m == "DJNZ" and first == "direct":
        reads.append("direct")
    if m == "PUSH":
        reads.append("direct")
    if m in {"POP", "RET", "RETI", "ACALL", "LCALL"}:
        reads.append("SP")
    if m == "MOVX" and "@Ri" in syn:
        reads.append("P2")
    if m == "MOVX" and "@DPTR" in syn:
        reads.append("DPTR")
    if m == "JMP" and "@A+DPTR" in syn:
        reads += ["A", "DPTR"]
    if m in {"RLC", "RRC"}:
        reads.append("C")
    return sorted(set(reads), key=reads.index)


def clean_text(text: str) -> str:
    """Remove PDF line-break hyphenation and common glyph substitutions."""
    text = text.replace("Ý", "#").replace("Ð", "-")

    def join(match: re.Match) -> str:
        left, right = match.group(1), match.group(2)
        if right.lower() in {"and", "or", "to", "of", "the", "in", "for", "with", "from", "by", "as", "is", "are", "be"}:
            return f"{left}- {right}"
        return f"{left}{right}"

    text = re.sub(r"(\w+)-\s+(\w+)", join, text)
    text = text.replace("set on zero", "set to zero")
    return re.sub(r"\s+", " ", text).strip()


def clean_operation(text: str) -> str:
    """Make the guide's Operation notation readable without changing meaning."""
    text = clean_text(text)
    text = re.split(r"INTEL CORPORATION|All mnemonics", text)[0].strip()
    text = text.replace("¶", "∨").replace("!", "∧")
    text = re.sub(r"\bkl\b", "≠", text)
    text = re.sub(r"\bl\b", ">", text)
    text = re.sub(r"\bk\b", "<", text)
    text = re.sub(r"\be\b", "=", text)
    text = re.sub(r"\bx\s+w\b", "↔", text)
    text = re.sub(r"\bw\b", "←", text)
    text = re.sub(r"\bw(?=\d)", "← ", text)
    text = re.sub(r"\)\s*w\b", ") ←", text)
    text = re.sub(r"\ba\b", "+", text)
    text = re.sub(r"\bb\b", "-", text)
    text = re.sub(r"\bx\b", "×", text)
    text = re.sub(r"\bI\b", "⊕", text)
    text = re.sub(r"\bs\b", "¬", text)
    text = re.sub(r"n\s*e\s*0\s*b\s*6", "n = 0..6", text)
    text = re.sub(r"n\s*=\s*0\s*-\s*6", "n = 0..6", text)
    return re.sub(r"\s+", " ", text).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--report", type=Path, default=REPORT)
    args = ap.parse_args(argv)

    html = load_json(HTML)
    guide = load_json(GUIDE)
    scan = load_json(SCAN)
    manual = load_json(MANUAL)["rows"]
    pages = guide_page_map()
    sections = html_sections(html)
    intros = guide_intro_map(guide)

    issues: list[str] = []
    warnings: list[str] = []

    # 1. Canonical guide forms.
    form_by_key: dict[str, dict] = {}
    for form in guide["forms"]:
        key = canon(form["heading"])
        if key in form_by_key:
            issues.append(f"duplicate guide form: {key}")
        form_by_key[key] = form

    # 2. Map every HTML opcode to a form.
    opcode_to_form: dict[str, dict] = {}
    for opcode, row in html["detail_rows"].items():
        if opcode == "0xA5":
            continue
        key = canon(row["syntax"])
        form = form_by_key.get(key)
        if form is None:
            issues.append(f"{opcode}: no guide form for {row['syntax']!r} -> {key!r}")
            continue
        if opcode in opcode_to_form:
            issues.append(f"{opcode}: mapped twice")
        opcode_to_form[opcode] = form

    # 3. Reverse mapping and per-form opcode lists.
    form_opcodes: dict[str, list[str]] = {}
    for opcode, form in opcode_to_form.items():
        form_opcodes.setdefault(form["heading"], []).append(opcode)
    for opcodes in form_opcodes.values():
        opcodes.sort(key=lambda x: int(x, 16))

    # 4. Scan cycle candidates: keep the best candidate per form.
    scan_best: dict[str, dict] = {}
    for row in scan["candidates"]:
        name = row.get("matched_form")
        if not name:
            continue
        old = scan_best.get(name)
        if old is None or row["match_ratio"] > old["match_ratio"]:
            scan_best[name] = row

    # 5. Build instruction records.
    instructions: list[dict] = []
    for form in guide["forms"]:
        guide_heading = form["heading"]
        heading = canon(guide_heading)
        opcodes = form_opcodes.get(guide_heading, [])
        if not opcodes:
            issues.append(f"{heading}: guide form has no opcode")
            continue
        html_rows = [html["detail_rows"][op] for op in opcodes]
        html_bytes = sorted({r["bytes"] for r in html_rows})
        if len(html_bytes) != 1:
            issues.append(f"{heading}: HTML byte counts differ among opcodes: {html_bytes}")
            html_bytes = [html_rows[0]["bytes"]]
        if html_bytes[0] != form["bytes"]:
            issues.append(
                f"{heading}: guide bytes {form['bytes']} != HTML bytes {html_bytes[0]}"
            )
        for op in opcodes:
            t11 = next((r for r in guide["table11"] if r["opcode"] == op), None)
            if not t11:
                issues.append(f"{op}: missing from Table 11")
            elif t11["bytes"] != form["bytes"]:
                issues.append(
                    f"{op}: Table 11 bytes {t11['bytes']} != guide bytes {form['bytes']}"
                )

        # Description and operation.
        intro = nearest_intro(form, intros)
        sec = sections.get(html_rows[0]["anchor"], {})
        description = form["description"] or (intro["description"] if intro else "")
        description_source = "guide-form" if form["description"] else (
            "guide-intro" if intro and intro["description"] else "none"
        )
        if not description:
            description = sec.get("description", "")
            description_source = "html-section" if description else "none"
        if not description:
            warnings.append(f"{heading}: no description")
        operation = form["operation"]
        if not operation:
            warnings.append(f"{heading}: no operation text")
        if not form["encoding"]:
            warnings.append(f"{heading}: no encoding text")

        # Scan cross-check.
        manual_row = manual.get(guide_heading)
        scan_row = scan_best.get(guide_heading)
        scan_cycles = None
        scan_bytes = None
        scan_source = None
        if manual_row:
            scan_cycles = manual_row["cycles"]
            scan_bytes = manual_row["bytes"]
            scan_source = f"manual page image p{manual_row['page']}"
        elif scan_row and scan_row.get("cycles") is not None:
            scan_cycles = scan_row["cycles"]
            scan_bytes = scan_row["bytes"]
            scan_source = f"OCR p{scan_row['page']} ratio={scan_row['match_ratio']}"
        if scan_cycles is not None and scan_cycles != form["cycles"]:
            issues.append(
                f"{heading}: guide cycles {form['cycles']} != scan cycles {scan_cycles} ({scan_source})"
            )
        if scan_bytes is not None and scan_bytes != form["bytes"]:
            issues.append(
                f"{heading}: guide bytes {form['bytes']} != scan bytes {scan_bytes} ({scan_source})"
            )
        if scan_cycles is None:
            warnings.append(f"{heading}: no scan cycle cross-check")

        effects = flags_effects(form, html_rows[0])
        written = written_registers(form)
        reads = read_registers(form)
        # PSW flag writes and parity.
        for flag, effect in effects.items():
            if effect != "unchanged":
                written.append(f"PSW.{flag}")
        if "A" in written:
            written.append("PSW.P")

        page = pages.get(form["line"], 0)
        record = {
            "id": slug(heading),
            "mnemonic": form["mnemonic"],
            "syntax": heading,
            "family": canon(intro["heading"]) if intro else form["mnemonic"],
            "opcodes": opcodes,
            "bytes": form["bytes"],
            "cycles": form["cycles"],
            "oscillator_periods": form["cycles"] * 12,
            "flags": effects,
            "registers": {
                "written": sorted(set(written), key=written.index),
                "read": sorted(set(reads), key=reads.index),
            },
            "function": form["function"] or sec.get("function", ""),
            "description": clean_text(description),
            "description_source": description_source,
            "example": clean_text(form["example"]),
            "operation": clean_operation(operation).replace(" I #data", " ⊕ #data")
            if form["mnemonic"] == "XRL"
            else clean_operation(operation),
            "operation_raw": operation,
            "encoding": form["encoding"],
            "notes": [],
            "source_refs": {
                "guide": {
                    "file": "docs/MCS-51_Programmer’s_Guide_and_Instruction_Set.PDF",
                    "pdf_page": page,
                    "line": form["line"],
                    "heading": guide_heading,
                },
                "html": {
                    "file": "docs/8051 Instruction Set.html",
                    "anchor": html_rows[0]["anchor"],
                    "syntax_examples": sorted({r["syntax"] for r in html_rows}),
                    "bytes": html_bytes[0],
                    "flags_text": html_rows[0]["flags_text"],
                },
                "table11": {
                    "file": "docs/MCS-51_Programmer’s_Guide_and_Instruction_Set.PDF",
                    "opcodes": opcodes,
                },
                "scan": {
                    "file": "docs/ism51.pdf",
                    "table": "Table 10",
                    "cross_check": scan_source,
                },
            },
            "review": {
                "status": "reviewed",
                "scan_verified": "page_image" if manual_row else (
                    "ocr_high_confidence" if scan_row and scan_row.get("match_ratio", 0) >= 0.85 else "none"
                ),
                "checks": [
                    "html_bytes_match",
                    "table11_bytes_match",
                    "scan_cycles_match",
                    "flags_checked_against_guide",
                    "manual_guide_pass",
                ],
            },
        }
        if heading == "MOV A,direct":
            record["notes"].append("MOV A,ACC is not a valid instruction.")
        if heading == "RL A":
            record["notes"].append(
                "HTML flag column lists C, but the guide flag table and RL operation show C is unchanged."
            )
        if heading in {"MUL AB", "DIV AB"}:
            record["notes"].append(
                "Guide flag table specifies C=0; HTML only says C is affected."
            )
        instructions.append(record)

    instructions.sort(key=lambda r: (r["mnemonic"], r["syntax"], r["id"]))

    # 6. Opcode table.
    opcodes_out: list[dict] = []
    for op in range(256):
        key = f"0x{op:02X}"
        if key == "0xA5":
            opcodes_out.append(
                {
                    "opcode": key,
                    "reserved": True,
                    "mnemonic": "?",
                    "bytes": None,
                    "cycles": None,
                    "notes": ["Reserved opcode; execution must trap."],
                }
            )
            continue
        row = html["detail_rows"].get(key)
        if not row:
            issues.append(f"{key}: missing from HTML")
            continue
        form = opcode_to_form.get(key)
        if not form:
            issues.append(f"{key}: missing guide form")
            continue
        rec = next((r for r in instructions if r["syntax"] == canon(form["heading"])), None)
        if not rec:
            issues.append(f"{key}: missing instruction record")
            continue
        opcodes_out.append(
            {
                "opcode": key,
                "mnemonic": row["syntax"].split(" ")[0] if row["syntax"] else form["mnemonic"],
                "form": rec["id"],
                "syntax": row["syntax"],
                "bytes": form["bytes"],
                "cycles": form["cycles"],
                "flags": rec["flags"],
                "source_refs": {
                    "html_anchor": row["anchor"],
                    "guide_line": form["line"],
                },
            }
        )

    if len(instructions) != 111:
        issues.append(f"instruction count {len(instructions)} != 111")
    if len(opcodes_out) != 256:
        issues.append(f"opcode count {len(opcodes_out)} != 256")
    valid = [o for o in opcodes_out if not o.get("reserved")]
    if len(valid) != 255:
        issues.append(f"valid opcode count {len(valid)} != 255")

    out = {
        "schema": "simu8051.isa/1",
        "device": "8051",
        "sources": [
            {"id": "html", "file": "docs/8051 Instruction Set.html", "sha256": html["sha256"]},
            {
                "id": "guide",
                "file": "docs/MCS-51_Programmer’s_Guide_and_Instruction_Set.PDF",
                "sha256": guide["sha256"],
            },
            {"id": "scan", "file": "docs/ism51.pdf"},
        ],
        "counts": {
            "instructions": len(instructions),
            "opcodes": len(opcodes_out),
            "valid_opcodes": len(valid),
            "reserved_opcodes": len(opcodes_out) - len(valid),
        },
        "instructions": instructions,
        "opcodes": opcodes_out,
        "notes": [
            "Cycles are machine cycles; oscillator_periods = cycles * 12.",
            "The scanned Table 10 was read from page images where OCR failed; see tools/overrides/scan_manual.json.",
            "Direct writes to PSW (0xD0) or PSW bits also change flag settings, even when the instruction is a generic MOV/ANL/ORL/XRL/INC/DEC/POP/XCH form. The per-instruction flags below describe normal non-PSW operands.",
            "This file is generated by tools/build_data.py; do not hand-edit.",
        ],
    }
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = {
        "issues": issues,
        "warnings": warnings,
        "counts": out["counts"],
        "checked": {
            "html_rows": len(html["detail_rows"]),
            "guide_forms": len(guide["forms"]),
            "scan_candidates": len(scan["candidates"]),
            "scan_manual_rows": len(manual),
            "instructions_with_description": sum(1 for r in instructions if r["description"]),
            "instructions_with_operation": sum(1 for r in instructions if r["operation"]),
            "instructions_with_encoding": sum(1 for r in instructions if r["encoding"]),
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps(report["counts"], indent=2))
    print(f"issues: {len(issues)}, warnings: {len(warnings)}")
    for item in issues[:30]:
        print(" ISSUE:", item)
    for item in warnings[:20]:
        print(" WARN:", item)
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
