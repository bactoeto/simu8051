#!/usr/bin/env python3
"""Compare per-opcode cycle counts with SDCC's ucsim_51.

ucsim prints oscillator ticks; 12 ticks = 1 8051 machine cycle.
This is a reference cross-check, not an authority. Differences must be
resolved against the Intel guide.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "build" / "ucsim_cases"
OPCODES = json.loads((ROOT / "data" / "isa.json").read_text(encoding="utf-8"))["opcodes"]
UCSIM = shutil.which("ucsim_51")
TICKS_RE = re.compile(r"stepped\s+(\d+)\s+ticks")


def hex_record(opcode: int) -> str:
    data = bytes([opcode, 0, 0])
    count = len(data)
    address = 0
    rectype = 0
    body = bytes([count, (address >> 8) & 0xFF, address & 0xFF, rectype]) + data
    checksum = (-sum(body)) & 0xFF
    return ":" + (body + bytes([checksum])).hex().upper()


def make_case(opcode: int) -> Path:
    path = CASES / f"{opcode:02X}.hex"
    path.write_text(hex_record(opcode) + "\n:00000001FF\n", encoding="ascii")
    return path


def run_case(path: Path) -> tuple[int | None, str]:
    proc = subprocess.run(
        [UCSIM, "-t", "51", "-q", "-e", f'load "{path}"', "-e", "step 1", "-e", "quit"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    match = TICKS_RE.search(proc.stdout)
    if not match:
        return None, proc.stdout + proc.stderr
    return int(match.group(1)), ""


def main() -> int:
    if UCSIM is None:
        print("ucsim_51 not found", file=sys.stderr)
        return 2
    CASES.mkdir(parents=True, exist_ok=True)
    expected = {int(item["opcode"], 16): item for item in OPCODES}
    mismatches: list[tuple[int, int | None, int, str]] = []
    unreadable: list[tuple[int, str]] = []
    for opcode in range(256):
        if opcode == 0xA5:
            continue
        path = make_case(opcode)
        ticks, error = run_case(path)
        entry = expected[opcode]
        if ticks is None:
            unreadable.append((opcode, error[-500:]))
            continue
        got = ticks // 12 if ticks % 12 == 0 else None
        want = entry["cycles"]
        if got != want:
            mismatches.append((opcode, got, want, entry["syntax"]))
    print(f"checked: {255 - len(unreadable)}/255")
    print(f"mismatches: {len(mismatches)}")
    for opcode, got, want, syntax in mismatches:
        print(f"  0x{opcode:02X} {syntax}: ucsim={got} data={want}")
    if unreadable:
        print(f"unreadable: {len(unreadable)}")
        for opcode, error in unreadable[:10]:
            print(f"  0x{opcode:02X}: {error.splitlines()[-1] if error.splitlines() else error}")
    return 0 if not mismatches and not unreadable else 1


if __name__ == "__main__":
    sys.exit(main())
