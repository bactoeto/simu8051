#!/usr/bin/env python3
"""Extract cycle candidates from the scanned Table 10.

The scan's OCR text is only used to locate candidates. Every number used in
the final data must be checked against the page image; this script records
page/line and a match ratio so mismatches can be reviewed.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from norm import canon

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TXT = ROOT / "build" / "ism51.ocr.txt"
DEFAULT_GUIDE = ROOT / "build" / "guide_facts.json"
DEFAULT_OUT = ROOT / "build" / "scan_facts.json"


def parse_half(half: str, page: int, line_no: int) -> dict | None:
    half = half.strip()
    if not half:
        return None
    m = re.match(r"^(.*?)\s+(\d+)\s+(\d+)\s*$", half)
    if m:
        text, nbytes, period = m.group(1), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(.*?)\s+(\d+)\s*$", half)
        if not m:
            return None
        text, nbytes, period = m.group(1), None, int(m.group(2))
    text = text.strip()
    m = re.match(r"^([A-Z]{2,6})\s*(.*)$", text)
    if not m:
        return None
    mnemonic, rest = m.group(1), m.group(2).strip()
    # The description follows the operands after two or more spaces.
    operands = re.split(r"\s{2,}", rest, maxsplit=1)[0].strip()
    candidate = canon(f"{mnemonic} {operands}")
    return {
        "page": page,
        "line": line_no,
        "raw": half,
        "mnemonic_ocr": mnemonic,
        "operands_ocr": operands,
        "candidate": candidate,
        "bytes": nbytes,
        "oscillator_period": period,
        "cycles": period // 12 if period % 12 == 0 else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--txt", type=Path, default=DEFAULT_TXT)
    ap.add_argument("--guide", type=Path, default=DEFAULT_GUIDE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    guide = json.loads(args.guide.read_text())
    form_keys = sorted({canon(f["heading"]) for f in guide["forms"]})
    form_by_key = {canon(f["heading"]): f for f in guide["forms"]}

    text = args.txt.read_text(encoding="utf-8", errors="replace")
    pages = text.split("\f")
    candidates: list[dict] = []
    for page_no in range(1, min(5, len(pages) + 1)):
        for line_no, line in enumerate(pages[page_no - 1].splitlines(), 1):
            # Two fixed-width columns; second starts around column 62.
            for half in (line[:62], line[62:]):
                row = parse_half(half, page_no, line_no)
                if row is None:
                    continue
                key = row["candidate"]
                if key in form_by_key:
                    row["matched_form"] = form_by_key[key]["heading"]
                    row["match_ratio"] = 1.0
                else:
                    best = difflib.get_close_matches(key, form_keys, n=1, cutoff=0.78)
                    if best:
                        row["matched_form"] = form_by_key[best[0]]["heading"]
                        row["match_ratio"] = round(
                            difflib.SequenceMatcher(None, key, best[0]).ratio(), 3
                        )
                    else:
                        row["matched_form"] = None
                        row["match_ratio"] = 0.0
                candidates.append(row)

    matched = [r for r in candidates if r["matched_form"] and r["match_ratio"] >= 0.9]
    out = {
        "source": str(args.txt.relative_to(ROOT)) if args.txt.is_relative_to(ROOT) else str(args.txt),
        "pages": "1-4 (PDF pages; Table 10)",
        "note": "OCR candidates only; verify numbers against page images before final use.",
        "candidates": candidates,
        "counts": {
            "candidates": len(candidates),
            "exact_or_high_confidence": len(matched),
            "unmatched": sum(1 for r in candidates if not r["matched_form"]),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps(out["counts"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
