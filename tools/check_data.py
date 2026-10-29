#!/usr/bin/env python3
"""Validate isa.json and memory.json against the task's data requirements."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_SFRS = {
    "P0": 0x80, "SP": 0x81, "DPL": 0x82, "DPH": 0x83,
    "PCON": 0x87, "TCON": 0x88, "TMOD": 0x89, "TL0": 0x8A,
    "TL1": 0x8B, "TH0": 0x8C, "TH1": 0x8D, "P1": 0x90,
    "SCON": 0x98, "SBUF": 0x99, "P2": 0xA0, "IE": 0xA8,
    "P3": 0xB0, "IP": 0xB8, "PSW": 0xD0, "ACC": 0xE0, "B": 0xF0,
}
BIT_ADDRESSABLE = {"P0", "TCON", "P1", "SCON", "P2", "IE", "P3", "IP", "PSW", "ACC", "B"}
FLAG_VALUES = {"set", "cleared", "modified", "unchanged"}


def check_isa(data: dict, errors: list[str]) -> dict:
    checks = {}
    if data.get("counts", {}).get("instructions") != 111:
        errors.append("isa: instruction count != 111")
    if data.get("counts", {}).get("valid_opcodes") != 255:
        errors.append("isa: valid opcode count != 255")
    if data.get("counts", {}).get("opcodes") != 256:
        errors.append("isa: opcode slot count != 256")

    opcodes = data["opcodes"]
    seen: set[str] = set()
    by_id = {i["id"]: i for i in data["instructions"]}
    if len(by_id) != len(data["instructions"]):
        errors.append("isa: duplicate instruction id")
    for op in opcodes:
        code = op["opcode"]
        if code in seen:
            errors.append(f"isa: duplicate opcode {code}")
        seen.add(code)
        if code == "0xA5":
            if not op.get("reserved"):
                errors.append("isa: 0xA5 not marked reserved")
            continue
        if op.get("reserved"):
            errors.append(f"isa: {code} unexpectedly reserved")
        form = by_id.get(op.get("form"))
        if not form:
            errors.append(f"isa: {code} points to missing form {op.get('form')}")
            continue
        if code not in form["opcodes"]:
            errors.append(f"isa: {code} not in form {form['id']} opcode list")
        if op["bytes"] != form["bytes"] or op["cycles"] != form["cycles"]:
            errors.append(f"isa: {code} bytes/cycles disagree with form")
    expected_codes = {f"0x{i:02X}" for i in range(256)}
    if seen != expected_codes:
        errors.append(f"isa: opcode coverage mismatch, missing={sorted(expected_codes-seen)[:5]}")

    for inst in data["instructions"]:
        if not inst["opcodes"]:
            errors.append(f"isa: {inst['id']} has no opcodes")
        if inst["bytes"] not in (1, 2, 3):
            errors.append(f"isa: {inst['id']} bad bytes {inst['bytes']}")
        if inst["cycles"] not in (1, 2, 4):
            errors.append(f"isa: {inst['id']} bad cycles {inst['cycles']}")
        if inst["oscillator_periods"] != inst["cycles"] * 12:
            errors.append(f"isa: {inst['id']} oscillator periods mismatch")
        if set(inst["flags"]) != {"C", "AC", "OV"}:
            errors.append(f"isa: {inst['id']} flags keys wrong")
        for flag, value in inst["flags"].items():
            if value not in FLAG_VALUES:
                errors.append(f"isa: {inst['id']} flag {flag} bad value {value}")
        for field in ("description", "operation", "encoding", "function"):
            if not inst.get(field):
                errors.append(f"isa: {inst['id']} empty {field}")
        if "PC" not in inst["registers"]["written"]:
            errors.append(f"isa: {inst['id']} does not list PC as written")
        for flag, value in inst["flags"].items():
            if value != "unchanged" and f"PSW.{flag}" not in inst["registers"]["written"]:
                errors.append(f"isa: {inst['id']} flag {flag} not in written registers")
        if "A" in inst["registers"]["written"] and "PSW.P" not in inst["registers"]["written"]:
            errors.append(f"isa: {inst['id']} writes A but not PSW.P")
        if not inst.get("source_refs", {}).get("guide"):
            errors.append(f"isa: {inst['id']} missing guide source ref")

    checks["instructions"] = len(data["instructions"])
    checks["valid_opcodes"] = len([o for o in opcodes if not o.get("reserved")])
    checks["forms_with_scan_page_image"] = sum(
        1 for i in data["instructions"] if i.get("review", {}).get("scan_verified") == "page_image"
    )
    return checks


def check_memory(data: dict, errors: list[str]) -> dict:
    checks = {}
    sfrs = {s["name"]: s for s in data["sfrs"]}
    if len(sfrs) != 21:
        errors.append(f"memory: SFR count {len(sfrs)} != 21")
    if set(sfrs) != set(EXPECTED_SFRS):
        errors.append(f"memory: SFR names differ: {set(sfrs) ^ set(EXPECTED_SFRS)}")
    addresses: set[int] = set()
    bit_addresses: set[int] = set()
    for name, expected_addr in EXPECTED_SFRS.items():
        sfr = sfrs.get(name)
        if not sfr:
            continue
        addr = int(sfr["address"], 16)
        if addr != expected_addr:
            errors.append(f"memory: {name} address {sfr['address']} != 0x{expected_addr:02X}")
        if addr in addresses:
            errors.append(f"memory: duplicate SFR address {sfr['address']}")
        addresses.add(addr)
        if not 0x80 <= addr <= 0xFF:
            errors.append(f"memory: {name} address outside SFR space")
        if bool(sfr["bit_addressable"]) != (name in BIT_ADDRESSABLE):
            errors.append(f"memory: {name} bit_addressable mismatch")
        if sfr["bit_addressable"]:
            if addr % 8 != 0:
                errors.append(f"memory: {name} bit-addressable but address not 0/8 aligned")
            if len(sfr["bits"]) != 8:
                errors.append(f"memory: {name} bit count {len(sfr['bits'])} != 8")
            for bit in sfr["bits"]:
                pos = bit["position"]
                if not 0 <= pos <= 7:
                    errors.append(f"memory: {name} bad bit position {pos}")
                bit_addr = int(bit["address"], 16)
                if bit_addr != addr + pos:
                    errors.append(f"memory: {name}.{pos} bit address mismatch")
                if bit_addr in bit_addresses:
                    errors.append(f"memory: duplicate bit address 0x{bit_addr:02X}")
                bit_addresses.add(bit_addr)
        elif sfr["bits"] and name not in {"PCON", "TMOD"}:
            errors.append(f"memory: non-bit-addressable {name} has bit list")

    reset = data["reset"]
    for key in ("pc", "sp", "acc", "b", "psw", "dptr", "p0", "p1", "p2", "p3",
                "ie", "ip", "tmod", "tcon", "th0", "tl0", "th1", "tl1",
                "scon", "sbuf", "pcon"):
        if key not in reset:
            errors.append(f"memory: reset missing {key}")
    for key, value in reset.items():
        if key in {"internal_ram", "external_ram", "notes", "source", "undefined_masks"}:
            continue
        if not isinstance(value, str):
            errors.append(f"memory: reset.{key} not a string")

    reset_to_sfr = {
        "acc": "ACC", "b": "B", "psw": "PSW", "sp": "SP",
        "p0": "P0", "p1": "P1", "p2": "P2", "p3": "P3",
        "ie": "IE", "ip": "IP", "tmod": "TMOD", "tcon": "TCON",
        "th0": "TH0", "tl0": "TL0", "th1": "TH1", "tl1": "TL1",
        "scon": "SCON", "pcon": "PCON",
    }
    for reset_key, sfr_name in reset_to_sfr.items():
        if sfr_name in sfrs:
            if int(reset[reset_key], 16) != int(sfrs[sfr_name]["reset"], 16):
                errors.append(
                    f"memory: reset.{reset_key} != {sfr_name} reset ({sfrs[sfr_name]['reset']})"
                )
    for sfr_name in ("IP", "IE", "PCON"):
        if sfr_name in sfrs and "reset_undefined_mask" not in sfrs[sfr_name]:
            errors.append(f"memory: {sfr_name} missing reset_undefined_mask")

    spaces = data["spaces"]
    for space in ("program", "iram", "sfr", "xdata", "bit"):
        if space not in spaces:
            errors.append(f"memory: missing space {space}")
    if spaces.get("sfr", {}).get("implemented_count") != 21:
        errors.append("memory: sfr space implemented_count != 21")
    if spaces["program"]["size"] != 65536:
        errors.append("memory: program size != 65536")
    if spaces["iram"]["size"] != 128:
        errors.append("memory: iram size != 128")
    if spaces["xdata"]["size"] != 65536:
        errors.append("memory: xdata size != 65536")
    if spaces["bit"]["size"] != 256:
        errors.append("memory: bit size != 256")

    irq = data["interrupts"]
    if [s["name"] for s in irq["sources"]] != ["IE0", "TF0", "IE1", "TF1", "RI_TI"]:
        errors.append("memory: interrupt source list/order wrong")
    if irq["natural_priority"] != ["IE0", "TF0", "IE1", "TF1", "RI_TI"]:
        errors.append("memory: natural priority wrong")
    if irq.get("serial_irq") != "disabled (no serial simulation)":
        errors.append("memory: serial interrupt policy not disabled")

    timers = data["timers"]
    if timers["timers"] != ["T0", "T1"]:
        errors.append("memory: timer list wrong")
    if set(timers["modes"]) != {"0", "1", "2", "3"}:
        errors.append("memory: timer modes incomplete")
    if data["serial"].get("simulated") is not False:
        errors.append("memory: serial simulated flag should be false")
    if any(s["name"] in {"T2CON", "TH2", "TL2", "RCAP2H", "RCAP2L"} for s in data["sfrs"]):
        errors.append("memory: 8052 Timer 2 SFR present")

    checks["sfrs"] = len(sfrs)
    checks["bit_addressable_sfrs"] = sum(1 for s in sfrs.values() if s["bit_addressable"])
    checks["unique_bit_addresses"] = len(bit_addresses)
    checks["spaces"] = len(spaces)
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--isa", type=Path, default=ROOT / "data" / "isa.json")
    ap.add_argument("--memory", type=Path, default=ROOT / "data" / "memory.json")
    ap.add_argument("--report", type=Path, default=ROOT / "build" / "reports" / "data_check.json")
    args = ap.parse_args(argv)

    isa = json.loads(args.isa.read_text(encoding="utf-8"))
    memory = json.loads(args.memory.read_text(encoding="utf-8"))
    errors: list[str] = []
    checks = {
        "isa": check_isa(isa, errors),
        "memory": check_memory(memory, errors),
    }
    report = {"ok": not errors, "errors": errors, "checks": checks}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
