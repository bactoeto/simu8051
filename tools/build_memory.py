#!/usr/bin/env python3
"""Build memory.json from the MCS-51 Programmer's Guide.

The guide's memory chapter, SFR table, reset table and timer/interrupt
chapters are the source. Entries that the guide marks undefined are kept as
undefined instead of being silently assigned a value.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "memory.json"

SRC = "docs/MCS-51_Programmer’s_Guide_and_Instruction_Set.PDF"


def bits(names: list[str], start: int = 0) -> list[dict]:
    return [
        {"name": name, "position": start + i, "reset": 0}
        for i, name in enumerate(names)
    ]


def sfr(
    name: str,
    address: int,
    reset: int | str,
    description: str,
    page: int,
    *,
    bit_addressable: bool = False,
    bit_names: list[str] | None = None,
    bit_notes: dict[int, str] | None = None,
    side_effects: list[str] | None = None,
    reset_undefined_mask: str | None = None,
    reset_variants: dict | None = None,
    reserved_bits: list[int] | None = None,
) -> dict:
    entry = {
        "name": name,
        "address": f"0x{address:02X}",
        "reset": reset if isinstance(reset, str) else f"0x{reset:02X}",
        "description": description,
        "bit_addressable": bit_addressable,
        "bits": [],
        "side_effects": side_effects or [],
        "source": {"file": SRC, "pdf_page": page},
    }
    if bit_names:
        bs = bits(bit_names)
        reset_value = reset if isinstance(reset, int) else 0
        for b in bs:
            b["address"] = (
                f"0x{address + b['position']:02X}" if bit_addressable else None
            )
            b["reset"] = (reset_value >> b["position"]) & 1
            b["description"] = (bit_notes or {}).get(b["position"], "")
        entry["bits"] = bs
    if reset_undefined_mask:
        entry["reset_undefined_mask"] = reset_undefined_mask
    if reset_variants:
        entry["reset_variants"] = reset_variants
    if reserved_bits:
        entry["reserved_bits"] = reserved_bits
    return entry


SFRS = [
    sfr(
        "P0", 0x80, 0xFF, "Port 0 latch.", 9,
        bit_addressable=True, bit_names=[f"P0.{i}" for i in range(8)],
        side_effects=["write updates latch; pin follows latch unless externally driven"],
    ),
    sfr("SP", 0x81, 0x07, "Stack Pointer.", 9,
        side_effects=["PUSH pre-increments; POP post-decrements"]),
    sfr("DPL", 0x82, 0x00, "Data Pointer low byte.", 9),
    sfr("DPH", 0x83, 0x00, "Data Pointer high byte.", 9),
    sfr(
        "PCON", 0x87, 0x00, "Power Control.", 12,
        bit_names=["IDL", "PD", "GF0", "GF1", "", "", "", "SMOD"],
        bit_notes={
            0: "Idle mode bit.",
            1: "Power-down bit (CHMOS).",
            2: "General purpose flag 0.",
            3: "General purpose flag 1.",
            7: "Double baud rate bit.",
        },
        reserved_bits=[4, 5, 6],
        reset_undefined_mask="0x7F (HMOS) / 0x0F (CHMOS)",
        reset_variants={"HMOS": "0x00 with bits 6-0 undefined", "CHMOS": "0x00 with bits 3-0 undefined"},
        side_effects=["IDL stops instruction fetch; PD stops the oscillator (not simulated)"],
    ),
    sfr(
        "TCON", 0x88, 0x00, "Timer/Counter Control.", 15,
        bit_addressable=True,
        bit_names=["IT0", "IE0", "IT1", "IE1", "TR0", "TF0", "TR1", "TF1"],
        bit_notes={
            0: "External interrupt 0 type: 0 level, 1 falling edge.",
            1: "External interrupt 0 edge flag; cleared by hardware on vector.",
            2: "External interrupt 1 type: 0 level, 1 falling edge.",
            3: "External interrupt 1 edge flag; cleared by hardware on vector.",
            4: "Timer 0 run control.",
            5: "Timer 0 overflow flag; cleared by hardware on vector.",
            6: "Timer 1 run control.",
            7: "Timer 1 overflow flag; cleared by hardware on vector.",
        },
    ),
    sfr(
        "TMOD", 0x89, 0x00, "Timer/Counter Mode Control.", 15,
        bit_names=["M0_0", "M1_0", "C/T0", "GATE0", "M0_1", "M1_1", "C/T1", "GATE1"],
        bit_notes={
            0: "Timer 0 mode bit 0.",
            1: "Timer 0 mode bit 1.",
            2: "Timer 0 counter/timer select.",
            3: "Timer 0 gate control.",
            4: "Timer 1 mode bit 0.",
            5: "Timer 1 mode bit 1.",
            6: "Timer 1 counter/timer select.",
            7: "Timer 1 gate control.",
        },
    ),
    sfr("TL0", 0x8A, 0x00, "Timer 0 low byte.", 9),
    sfr("TL1", 0x8B, 0x00, "Timer 1 low byte.", 9),
    sfr("TH0", 0x8C, 0x00, "Timer 0 high byte.", 9),
    sfr("TH1", 0x8D, 0x00, "Timer 1 high byte.", 9),
    sfr(
        "P1", 0x90, 0xFF, "Port 1 latch.", 9,
        bit_addressable=True, bit_names=[f"P1.{i}" for i in range(8)],
        side_effects=["write updates latch; pin follows latch unless externally driven"],
    ),
    sfr(
        "SCON", 0x98, 0x00, "Serial Port Control.", 20,
        bit_addressable=True,
        bit_names=["RI", "TI", "RB8", "TB8", "REN", "SM2", "SM1", "SM0"],
        bit_notes={
            0: "Receive interrupt flag (plain bit in this simulator).",
            1: "Transmit interrupt flag (plain bit in this simulator).",
            2: "Ninth received data bit / stop bit.",
            3: "Ninth transmitted data bit.",
            4: "Receive enable.",
            5: "Multiprocessor mode control.",
            6: "Serial mode bit 1.",
            7: "Serial mode bit 0.",
        },
        side_effects=["plain storage only; no serial shift, baud rate or serial interrupt"],
    ),
    sfr("SBUF", 0x99, "indeterminate", "Serial Data Buffer.", 9,
        side_effects=["plain storage only; no receive/transmit side effect"]),
    sfr(
        "P2", 0xA0, 0xFF, "Port 2 latch.", 9,
        bit_addressable=True, bit_names=[f"P2.{i}" for i in range(8)],
        side_effects=["high byte of MOVX @Ri address comes from P2 latch"],
    ),
    sfr(
        "IE", 0xA8, 0x00, "Interrupt Enable.", 13,
        bit_addressable=True,
        bit_names=["EX0", "ET0", "EX1", "ET1", "ES", "", "", "EA"],
        bit_notes={
            0: "Enable external interrupt 0.",
            1: "Enable timer 0 interrupt.",
            2: "Enable external interrupt 1.",
            3: "Enable timer 1 interrupt.",
            4: "Enable serial interrupt (no serial interrupt source in this simulator).",
            5: "Reserved on 8051; ET2 on 8052.",
            6: "Reserved.",
            7: "Enable all interrupts.",
        },
        reserved_bits=[5, 6],
        reset_undefined_mask="0x60",
    ),
    sfr(
        "P3", 0xB0, 0xFF, "Port 3 latch.", 9,
        bit_addressable=True,
        bit_names=["P3.0", "P3.1", "P3.2", "P3.3", "P3.4", "P3.5", "P3.6", "P3.7"],
        bit_notes={
            0: "RXD (serial receive).",
            1: "TXD (serial transmit).",
            2: "INT0 external interrupt 0.",
            3: "INT1 external interrupt 1.",
            4: "T0 external timer 0 input.",
            5: "T1 external timer 1 input.",
            6: "WR external data write strobe.",
            7: "RD external data read strobe.",
        },
    ),
    sfr(
        "IP", 0xB8, 0x00, "Interrupt Priority.", 14,
        bit_addressable=True,
        bit_names=["PX0", "PT0", "PX1", "PT1", "PS", "", "", ""],
        bit_notes={
            0: "External interrupt 0 priority.",
            1: "Timer 0 priority.",
            2: "External interrupt 1 priority.",
            3: "Timer 1 priority.",
            4: "Serial interrupt priority.",
            5: "Reserved on 8051; PT2 on 8052.",
        },
        reserved_bits=[5, 6, 7],
        reset_undefined_mask="0xE0",
    ),
    sfr(
        "PSW", 0xD0, 0x00, "Program Status Word.", 12,
        bit_addressable=True,
        bit_names=["P", "F1", "OV", "RS0", "RS1", "F0", "AC", "CY"],
        bit_notes={
            0: "Parity flag; maintained by hardware from ACC.",
            1: "User flag F1.",
            2: "Overflow flag.",
            3: "Register bank select bit 0.",
            4: "Register bank select bit 1.",
            5: "User flag F0.",
            6: "Auxiliary carry flag.",
            7: "Carry flag.",
        },
        side_effects=[
            "RS1/RS0 select the active register bank",
            "writing PSW changes C/AC/F0/RS1/RS0/OV/F1; P is maintained from ACC",
            "direct writes to PSW or PSW bits affect flags even from generic MOV/ANL/ORL/XRL/INC/DEC/POP/XCH forms",
        ],
    ),
    sfr(
        "ACC", 0xE0, 0x00, "Accumulator.", 9,
        bit_addressable=True, bit_names=[f"ACC.{i}" for i in range(8)],
        side_effects=["PSW.P is updated whenever ACC is written"],
    ),
    sfr(
        "B", 0xF0, 0x00, "B Register.", 9,
        bit_addressable=True, bit_names=[f"B.{i}" for i in range(8)],
        side_effects=["used by MUL and DIV"],
    ),
]

RESET = {
    "pc": "0x0000",
    "sp": "0x07",
    "acc": "0x00",
    "b": "0x00",
    "psw": "0x00",
    "dptr": "0x0000",
    "p0": "0xFF",
    "p1": "0xFF",
    "p2": "0xFF",
    "p3": "0xFF",
    "ie": "0x00",
    "ip": "0x00",
    "tmod": "0x00",
    "tcon": "0x00",
    "th0": "0x00",
    "tl0": "0x00",
    "th1": "0x00",
    "tl1": "0x00",
    "scon": "0x00",
    "sbuf": "indeterminate",
    "pcon": "0x00",
    "internal_ram": "undefined by the datasheet",
    "external_ram": "undefined by the datasheet",
    "undefined_masks": {
        "IP": "0xE0 (bits 7-5 undefined on 8051)",
        "IE": "0x60 (bits 6-5 undefined on 8051)",
        "PCON": "0x7F (HMOS) / 0x0F (CHMOS)",
    },
    "notes": [
        "Table 2 of the guide gives SFR values after power-on or reset.",
        "Internal RAM contents are not specified by the guide; a simulator must choose a deterministic policy.",
        "The 8051 has no Timer 2 SFRs.",
    ],
    "source": {"file": SRC, "pdf_page": 10, "table": "Table 2"},
}

MEMORY = {
    "schema": "simu8051.memory/1",
    "device": "8051",
    "sources": [
        {"id": "guide", "file": SRC},
    ],
    "timing": {
        "machine_cycle_oscillator_periods": 12,
        "phase_model": "none",
        "instruction_cycles": "fixed for every 8051 instruction",
        "notes": [
            "The simulator counts machine cycles; it does not expose S1-S6 phases.",
            "The scanned Table 10 expresses timing as oscillator periods (12/24/48), equivalent to 1/2/4 machine cycles.",
        ],
        "source": {"file": SRC, "pdf_page": 15, "section": "Timer/Interrupt timing and Table 10"},
    },
    "spaces": {
        "program": {
            "range": ["0x0000", "0xFFFF"],
            "size": 65536,
            "on_chip_8051": 4096,
            "reset_pc": "0x0000",
            "vectors": {
                "IE0": "0x0003",
                "TF0": "0x000B",
                "IE1": "0x0013",
                "TF1": "0x001B",
                "RI_TI": "0x0023",
            },
            "notes": [
                "Program memory is separate from data memory.",
                "The lower 4K may be on-chip on the 8051.",
                "Interrupt vector addresses are fixed.",
            ],
            "source": {"file": SRC, "pdf_page": 4},
        },
        "iram": {
            "range": ["0x00", "0x7F"],
            "size": 128,
            "regions": [
                {"name": "register_banks", "range": ["0x00", "0x1F"], "banks": 4, "note": "8 registers per bank"},
                {"name": "bit_addressable", "range": ["0x20", "0x2F"], "bits": 128},
                {"name": "scratch_pad", "range": ["0x30", "0x7F"]},
            ],
            "direct_and_indirect": True,
            "upper_128": "not implemented on 8051; indirect access to 0x80-0xFF is not internal RAM",
            "notes": [
                "The 8051 has 128 bytes of on-chip RAM; the 8052 has 256.",
                "Stack operations use indirect addressing.",
            ],
            "source": {"file": SRC, "pdf_page": 5},
        },
        "sfr": {
            "range": ["0x80", "0xFF"],
            "direct_only": True,
            "implemented_count": len(SFRS),
            "address_rule": "SFRs are accessed by direct addressing; bit-addressable SFR addresses end in 0 or 8",
            "unimplemented_policy": "read 0xFF, write ignored",
            "source": {"file": SRC, "pdf_pages": [9, 11]},
        },
        "xdata": {
            "range": ["0x0000", "0xFFFF"],
            "size": 65536,
            "access": "MOVX @DPTR or MOVX @Ri",
            "movx_ri_high_byte": "P2 latch",
            "source": {"file": SRC, "pdf_page": 5},
        },
        "bit": {
            "range": ["0x00", "0xFF"],
            "size": 256,
            "ram_bits": {
                "range": ["0x00", "0x7F"],
                "maps_to": ["0x20", "0x2F"],
                "formula": "byte = 0x20 + (bit // 8); mask = 1 << (bit % 8)",
            },
            "sfr_bits": {
                "range": ["0x80", "0xFF"],
                "rule": "bit address = SFR byte address + bit position; only bit-addressable SFRs",
                "sfrs": ["P0", "TCON", "P1", "SCON", "P2", "IE", "P3", "IP", "PSW", "ACC", "B"],
            },
            "source": {"file": SRC, "pdf_pages": [7, 11]},
        },
    },
    "sfrs": SFRS,
    "reset": RESET,
    "ports": {
        "latches": ["P0", "P1", "P2", "P3"],
        "reset_latch": "0xFF",
        "read_pin": "ordinary byte reads and bit tests of P0-P3 return the pin level",
        "read_latch": "read-modify-write instructions read the latch",
        "write": "writes update the latch; pins follow at the machine-cycle boundary",
        "external_drive": "simulator/plugin may override pin levels; latch is not modified",
        "movx_ri_high_byte": "P2 latch, not P2 pin",
        "electrical_model": "none (no pull-up strength, open drain or floating input)",
        "source": {"file": SRC, "pdf_pages": [7, 9]},
    },
    "stack": {
        "location": "internal RAM",
        "reset_sp": "0x07",
        "push_order": "SP = SP + 1; write to (SP)",
        "pop_order": "read (SP); SP = SP - 1",
        "sp_width": 8,
        "wraps": True,
        "upper_128_on_8051": "not available as stack space",
        "lcall_push_order": "PC low byte first, then PC high byte",
        "ret_pop_order": "PC high byte first, then PC low byte",
        "pop_sp_note": "POP SP decrements SP before loading the popped value (guide example).",
        "source": {"file": SRC, "pdf_pages": [7, 29]},
    },
    "interrupts": {
        "sources": [
            {"name": "IE0", "vector": "0x0003", "enable": "EX0", "priority": "PX0", "flag": "TCON.IE0", "trigger": "external INT0 (P3.2), level or falling edge by IT0"},
            {"name": "TF0", "vector": "0x000B", "enable": "ET0", "priority": "PT0", "flag": "TCON.TF0", "trigger": "Timer 0 overflow"},
            {"name": "IE1", "vector": "0x0013", "enable": "EX1", "priority": "PX1", "flag": "TCON.IE1", "trigger": "external INT1 (P3.3), level or falling edge by IT1"},
            {"name": "TF1", "vector": "0x001B", "enable": "ET1", "priority": "PT1", "flag": "TCON.TF1", "trigger": "Timer 1 overflow"},
            {"name": "RI_TI", "vector": "0x0023", "enable": "ES", "priority": "PS", "flag": "SCON.RI / SCON.TI", "trigger": "serial (not simulated; SFR bits are plain storage)"},
        ],
        "natural_priority": ["IE0", "TF0", "IE1", "TF1", "RI_TI"],
        "priority_levels": 2,
        "same_level_blocks": True,
        "higher_level_preempts": True,
        "hardware_cleared_flags": ["IE0 (edge)", "IE1 (edge)", "TF0", "TF1"],
        "level_triggered_flag": "not cleared by vector; re-requested while pin remains low",
        "reti_behavior": "RETI restores the interrupt logic to accept the same priority level; a pending lower/same-level interrupt is processed after one more instruction.",
        "ie_bits": {"EA": 7, "ES": 4, "ET1": 3, "EX1": 2, "ET0": 1, "EX0": 0},
        "ip_bits": {"PS": 4, "PT1": 3, "PX1": 2, "PT0": 1, "PX0": 0},
        "serial_irq": "disabled (no serial simulation)",
        "source": {"file": SRC, "pdf_pages": [13, 14]},
    },
    "timers": {
        "timers": ["T0", "T1"],
        "modes": {
            "0": "13-bit timer/counter (THx 8 bits + TLx low 5 bits)",
            "1": "16-bit timer/counter",
            "2": "8-bit auto-reload (TLx reloaded from THx)",
            "3": "T0 split into TL0 and TH0; T1 stopped (T1 mode 3 does not run)",
        },
        "mode3_details": {
            "TL0": "8-bit timer/counter controlled by the Timer 0 control bits (TR0, TF0, GATE, INT0, C/T)",
            "TH0": "8-bit timer controlled by the Timer 1 control bits (TR1, TF1); counts machine cycles",
            "T1": "stopped; TR1/TF1 are used by TH0",
        },
        "control": {
            "TR0": "TCON.4", "TF0": "TCON.5",
            "TR1": "TCON.6", "TF1": "TCON.7",
            "GATE": "TMOD.GATE0/GATE1", "C_T": "TMOD.C/T0, TMOD.C/T1",
            "M1_M0": "TMOD mode select bits",
        },
        "gate": "when GATE=1, TRx=1 and INTx pin high are both required",
        "counter_input": "falling edge on T0/T1 pin; sampled per machine cycle",
        "overflow": "sets TFx; hardware clears TFx when the interrupt is vectored",
        "source": {"file": SRC, "pdf_pages": [15, 16, 17]},
    },
    "serial": {
        "simulated": False,
        "sfrs": ["SCON", "SBUF"],
        "behavior": "plain registers; no shift register, no baud rate, no TI/RI hardware setting, no serial interrupt",
        "source": {"file": SRC, "pdf_pages": [20]},
    },
    "unsupported": [
        {"feature": "8052 Timer 2", "reason": "device is 8051"},
        {"feature": "serial port", "reason": "task.md explicitly excludes serial simulation"},
        {"feature": "S1-S6 phases", "reason": "cycle-level simulation without phases"},
        {"feature": "ALE/PSEN/RD/WR bus timing", "reason": "no external bus model"},
    ],
    "notes": [
        "The guide covers 8051/8052/80C51; this file is restricted to 8051 SFRs.",
        "Reserved SFR bits are documented but not assigned a defined value.",
        "Unimplemented SFR and indirect-0x80-0xFF policies are simulator decisions and must be tested.",
    ],
    "review": {
        "status": "reviewed",
        "checks": [
            "SFR addresses checked against Table 1",
            "reset values checked against Table 2",
            "bit fields checked against the SFR descriptions",
            "timer/interrupt definitions checked against the guide chapters",
            "8052-only SFRs excluded",
        ],
    },
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    args.out.write_text(json.dumps(MEMORY, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps({"sfrs": len(SFRS), "spaces": len(MEMORY["spaces"])}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
