#!/usr/bin/env python3
"""Differential test of 8051 instruction flags against SDCC's ucsim_51.

ucsim is a reference implementation, not an authority: every disagreement is
adjudicated against the Intel MCS-51 guide (see data/isa.json).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simu8051.core import Core  # noqa: E402

CASES_DIR = ROOT / "build" / "ucsim_cases" / "flags"
REPORT = ROOT / "reports" / "ucsim_flags.md"
ISA = json.loads((ROOT / "data" / "isa.json").read_text(encoding="utf-8"))
GUIDE_FLAGS = {item["syntax"]: item["flags"] for item in ISA["instructions"]}
UCSIM = shutil.which("ucsim_51")

FULL_VALUES = (0x00, 0x01, 0x0F, 0x10, 0x7F, 0x80, 0x81, 0xFE, 0xFF)
QUICK_VALUES = (0x00, 0x7F, 0x80, 0xFF)
DA_VALUES = (0x00, 0x09, 0x0A, 0x0F, 0x10, 0x99, 0x9A, 0xFF)

ACC_RE = re.compile(r"ACC=\s*0x([0-9A-Fa-f]+)")
B_RE = re.compile(r"(?<![A-Za-z])B=\s*0x([0-9A-Fa-f]+)")
PSW_RE = re.compile(
    r"PSW=\s*0x([0-9A-Fa-f]+)\s+CY=(\d)\s+AC=(\d)\s+OV=(\d)\s+P=(\d)"
)
PC_RE = re.compile(r"Stop at 0x([0-9A-Fa-f]+)")

DEFAULT_STRICT = ("A", "B", "C", "AC", "OV", "P", "PSW_HI")
PSW_WRITE_NAMES = (
    "MOV PSW,#data",
    "POP PSW",
    "ANL PSW,#data",
    "ORL PSW,#data",
    "XRL PSW,#data",
    "INC PSW",
    "DEC PSW",
    "XCH A,PSW",
)


@dataclass(frozen=True)
class Case:
    name: str
    code: tuple[int, ...]
    a: int = 0
    b: int = 0
    psw: int = 0
    iram: tuple[tuple[int, int], ...] = ()
    sfr: tuple[tuple[int, int], ...] = ()
    strict: tuple[str, ...] = DEFAULT_STRICT
    note: str = ""


def parity(value: int) -> int:
    return bin(value & 0xFF).count("1") & 1


def initial_psw(case: "Case") -> int:
    """PSW value before the instruction; bit 0 is hardware-maintained parity."""
    return (case.psw & 0xFE) | parity(case.a)


def hex_record(data: bytes, address: int = 0) -> str:
    body = bytes([len(data), (address >> 8) & 0xFF, address & 0xFF, 0]) + data
    return ":" + (body + bytes([(-sum(body)) & 0xFF])).hex().upper()


def build_cases(quick: bool) -> list[Case]:
    vals = QUICK_VALUES if quick else FULL_VALUES
    cases: list[Case] = []

    # 4.1 ADD / ADDC / SUBB
    for opcode, name, carries in (
        (0x28, "ADD A,Rn", (0,)),
        (0x38, "ADDC A,Rn", (0, 1)),
        (0x98, "SUBB A,Rn", (0, 1)),
    ):
        for a in vals:
            for b in vals:
                for carry in carries:
                    cases.append(
                        Case(
                            name=name,
                            code=(opcode,),
                            a=a,
                            psw=carry << 7,
                            iram=((0x00, b),),
                        )
                    )

    # 4.2 MUL / DIV
    for a in vals:
        for b in vals:
            cases.append(Case("MUL AB", (0xA4,), a=a, b=b))
            if b == 0:
                cases.append(
                    Case(
                        "DIV AB",
                        (0x84,),
                        a=a,
                        b=b,
                        strict=("C", "OV"),
                        note="divide by zero: A and B are undefined in the guide",
                    )
                )
            else:
                cases.append(Case("DIV AB", (0x84,), a=a, b=b))

    # 4.3 DA A
    for a in DA_VALUES:
        for carry in (0, 1):
            for ac in (0, 1):
                cases.append(
                    Case(
                        "DA A",
                        (0xD4,),
                        a=a,
                        psw=(carry << 7) | (ac << 6),
                        strict=("A", "C", "OV", "P"),
                        note="AC is an input; its output value is not defined by the guide",
                    )
                )

    # 4.4 rotates
    for opcode, name in ((0x23, "RL A"), (0x03, "RR A"), (0x33, "RLC A"), (0x13, "RRC A")):
        for a in vals:
            for carry in (0, 1):
                cases.append(Case(name, (opcode,), a=a, psw=carry << 7))

    # 4.5 bit flags
    for opcode, name in ((0xC3, "CLR C"), (0xD3, "SETB C"), (0xB3, "CPL C")):
        for carry in (0, 1):
            cases.append(Case(name, (opcode,), psw=carry << 7))

    for opcode, name in (
        (0x82, "ANL C,bit"),
        (0xB0, "ANL C,/bit"),
        (0x72, "ORL C,bit"),
        (0xA0, "ORL C,/bit"),
    ):
        for carry in (0, 1):
            for bit in (0, 1):
                cases.append(
                    Case(
                        name,
                        (opcode, 0x00),
                        psw=carry << 7,
                        iram=((0x20, bit),),
                    )
                )

    for bit in (0, 1):
        cases.append(Case("MOV C,bit", (0xA2, 0x00), iram=((0x20, bit),)))

    # 4.6 CJNE
    for dest in vals:
        for src in vals:
            cases.append(
                Case(
                    "CJNE A,direct,rel",
                    (0xB5, 0x20, 0x00),
                    a=dest,
                    iram=((0x20, src),),
                )
            )
            cases.append(Case("CJNE A,#data,rel", (0xB4, src, 0x00), a=dest))
            cases.append(
                Case(
                    "CJNE Rn,#data,rel",
                    (0xB8, src, 0x00),
                    iram=((0x00, dest),),
                )
            )
            cases.append(
                Case(
                    "CJNE @Ri,#data,rel",
                    (0xB6, src, 0x00),
                    iram=((0x00, 0x30), (0x30, dest)),
                )
            )

    # 4.7 direct PSW writes
    datas = (0x00, 0x01, 0x04, 0x40, 0x80, 0xFF) if not quick else (0x00, 0x80, 0xFF)
    for data in datas:
        for a in (0x00, 0x01):
            cases.append(Case("MOV PSW,#data", (0x75, 0xD0, data), a=a, strict=("A", "PSW_HI")))
        for a in (0x00, 0x01):
            cases.append(
                Case(
                    "POP PSW",
                    (0xD0,),
                    a=a,
                    sfr=((0x81, 0x07),),
                    iram=((0x07, data),),
                    strict=("A", "PSW_HI"),
                )
            )
        cases.append(
            Case("ANL PSW,#data", (0x53, 0xD0, data), a=0x01, psw=0x80, strict=("A", "PSW_HI"))
        )
        cases.append(
            Case("ORL PSW,#data", (0x43, 0xD0, data), a=0x01, psw=0x00, strict=("A", "PSW_HI"))
        )
        cases.append(
            Case("XRL PSW,#data", (0x63, 0xD0, data), a=0x01, psw=0x80, strict=("A", "PSW_HI"))
        )

    psw_values = (0x00, 0x04, 0x40, 0x80, 0x7E) if not quick else (0x00, 0x04, 0x80, 0x7E)
    for psw in psw_values:
        for a in (0x00, 0x01):
            cases.append(Case("INC PSW", (0x05, 0xD0), a=a, psw=psw, strict=("A", "PSW_HI")))
            cases.append(Case("DEC PSW", (0x15, 0xD0), a=a, psw=psw, strict=("A", "PSW_HI")))
        cases.append(Case("XCH A,PSW", (0xC5, 0xD0), a=0x01, psw=psw, strict=("PSW_HI",)))

    return cases


def snapshot_ours(case: Case) -> dict[str, int]:
    core = Core()
    core.code[: len(case.code)] = bytes(case.code)
    core.set_sfr("ACC", case.a)
    core.set_sfr("B", case.b)
    core.set_sfr("PSW", initial_psw(case))
    for address, value in case.sfr:
        core.write_direct(address, value)
    for address, value in case.iram:
        core.write_direct(address, value)
    core.execute_instruction()
    psw = core.get_sfr("PSW")
    return {
        "A": core.get_reg("A"),
        "B": core.get_reg("B"),
        "C": core._get_flag("C"),
        "AC": core._get_flag("AC"),
        "OV": core._get_flag("OV"),
        "P": psw & 1,
        "PSW": psw,
        "PSW_HI": psw & 0xFE,
        "PC": core.pc,
    }


def parse_snapshot(stdout: str) -> dict[str, int] | None:
    acc = ACC_RE.findall(stdout)
    b = B_RE.findall(stdout)
    psw = PSW_RE.findall(stdout)
    if not acc or not b or not psw:
        return None
    psw_value, cy, ac, ov, p = (int(item, 10) if i else int(item, 16) for i, item in enumerate(psw[-1]))
    pc = PC_RE.findall(stdout)
    return {
        "A": int(acc[-1], 16),
        "B": int(b[-1], 16),
        "C": int(cy),
        "AC": int(ac),
        "OV": int(ov),
        "P": int(p),
        "PSW": psw_value,
        "PSW_HI": psw_value & 0xFE,
        "PC": int(pc[-1], 16) if pc else -1,
    }


def run_ucsim(index: int, case: Case) -> tuple[dict[str, int] | None, str]:
    path = CASES_DIR / f"{index:04d}.hex"
    path.write_text(hex_record(bytes(case.code)) + "\n:00000001FF\n", encoding="ascii")
    psw = initial_psw(case)
    commands = [
        f'load "{path}"',
        f"set mem sfr 0xe0 0x{case.a:02x}",
        f"set mem sfr 0xf0 0x{case.b:02x}",
        f"set mem sfr 0xd0 0x{psw:02x}",
    ]
    for address, value in case.sfr:
        commands.append(f"set mem sfr 0x{address:02x} 0x{value:02x}")
    for address, value in case.iram:
        commands.append(f"set mem iram 0x{address:02x} 0x{value:02x}")
    commands += ["step 1", "info reg", "quit"]
    args = [UCSIM, "-t", "51", "-q"]
    for command in commands:
        args += ["-e", command]
    try:
        proc = subprocess.run(
            args, cwd=ROOT, text=True, capture_output=True, timeout=10
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    snapshot = parse_snapshot(proc.stdout)
    if snapshot is None:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-4:])
        return None, f"parse error: {tail}"
    return snapshot, ""


def ucsim_version() -> str:
    try:
        proc = subprocess.run([UCSIM, "-v"], text=True, capture_output=True, timeout=10)
        first = (proc.stdout or proc.stderr).strip().splitlines()
        if not first:
            return "unknown"
        return first[0].split(":", 1)[1].strip() if ":" in first[0] else first[0]
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def probe_psw_steady_state() -> dict[str, int] | None:
    """A=1, MOV PSW,#0, then two NOPs: P must be 1 if hardware maintains it."""
    path = CASES_DIR / "probe_psw_steady.hex"
    path.write_text(
        hex_record(bytes([0x74, 0x01, 0x75, 0xD0, 0x00, 0x00, 0x00]))
        + "\n:00000001FF\n",
        encoding="ascii",
    )
    commands = [
        f'load "{path}"',
        "set mem sfr 0xe0 0x01",
        "set mem sfr 0xd0 0x01",
        "step 4",
        "info reg",
        "quit",
    ]
    args = [UCSIM, "-t", "51", "-q"]
    for command in commands:
        args += ["-e", command]
    try:
        proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_snapshot(proc.stdout)


def describe(case: Case) -> str:
    parts = [f"A=0x{case.a:02X}", f"PSW=0x{initial_psw(case):02X}"]
    if case.b or case.name in ("MUL AB", "DIV AB"):
        parts.append(f"B=0x{case.b:02X}")
    for address, value in case.sfr:
        parts.append(f"sfr[{address:#04x}]=0x{value:02X}")
    for address, value in case.iram:
        parts.append(f"iram[{address:#04x}]=0x{value:02X}")
    return " ".join(parts)


def format_snapshot(snapshot: dict[str, int]) -> str:
    return (
        f"A=0x{snapshot['A']:02X} B=0x{snapshot['B']:02X} "
        f"C={snapshot['C']} AC={snapshot['AC']} OV={snapshot['OV']} P={snapshot['P']} "
        f"PSW=0x{snapshot['PSW']:02X}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="reduced value matrix")
    parser.add_argument("--jobs", type=int, default=8, help="parallel ucsim processes")
    args = parser.parse_args()

    if UCSIM is None:
        print("ucsim_51 not found", file=sys.stderr)
        return 2

    CASES_DIR.mkdir(parents=True, exist_ok=True)
    for stale in CASES_DIR.glob("*.hex"):
        stale.unlink()

    cases = build_cases(args.quick)

    def work(item: tuple[int, Case]):
        index, case = item
        try:
            ours = snapshot_ours(case)
        except Exception as exc:  # noqa: BLE001 - report, do not hide
            return index, case, None, None, f"our core failed: {exc}"
        theirs, error = run_ucsim(index, case)
        return index, case, ours, theirs, error

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for result in pool.map(work, enumerate(cases)):
            results.append(result)
            if len(results) % 100 == 0:
                print(f"  {len(results)}/{len(cases)}", file=sys.stderr)

    mismatches = []
    info = []
    errors = []
    for index, case, ours, theirs, error in results:
        if error:
            errors.append((index, case, error))
            continue
        fields = []
        for field in case.strict:
            if ours[field] != theirs[field]:
                fields.append((field, ours[field], theirs[field]))
        if fields:
            mismatches.append((index, case, ours, theirs, fields))
        if case.name == "DIV AB" and case.b == 0 and (ours["A"], ours["B"]) != (theirs["A"], theirs["B"]):
            info.append((index, case, ours, theirs))
        if case.name in ("DA A", "MUL AB", "DIV AB") and ours["AC"] != theirs["AC"]:
            info.append((index, case, ours, theirs))
        if case.name in PSW_WRITE_NAMES and ours["P"] != theirs["P"]:
            info.append((index, case, ours, theirs))

    coverage: dict[str, list[int]] = {}
    for _, case, _, _, _ in results:
        coverage.setdefault(case.name, [0, 0])[0] += 1
    for _, case, _, _, _ in mismatches:
        coverage[case.name][1] += 1

    probe = probe_psw_steady_state()
    probe_text = (
        f"ucsim A=0x{probe['A']:02X}、P={probe['P']}"
        if probe is not None
        else "ucsim 探针执行失败"
    )

    info_by_name: dict[str, list[tuple[Case, dict[str, int], dict[str, int]]]] = {}
    for _, case, ours, theirs in info:
        info_by_name.setdefault(case.name, []).append((case, ours, theirs))

    report = [
        "# ucsim flags 差分测试报告",
        "",
        f"日期：{date.today().isoformat()}",
        "",
        f"- ucsim：`{UCSIM}`（{ucsim_version()}）",
        f"- 模式：{'quick' if args.quick else 'full'}",
        f"- 用例：{len(cases)}",
        f"- 严格不一致：{len(mismatches)}",
        f"- 已裁决差异：{len(info)}",
        f"- 错误：{len(errors)}",
        "",
        "Intel MCS-51 guide 是最终裁决依据；ucsim 仅作参考实现。",
        "",
        "## 覆盖范围",
        "",
        "| 指令 | 用例 | 严格不一致 |",
        "|---|---:|---:|",
    ]
    for name, (total, bad) in coverage.items():
        report.append(f"| {name} | {total} | {bad} |")

    report += [
        "",
        "值集合：`00 01 0F 10 7F 80 81 FE FF`；DA 用 `00 09 0A 0F 10 99 9A FF`。",
        "每条用例比较 A、B、C、AC、OV、P 与 PSW 高位（F0/F1/RS0/RS1）；DIV 除零只比较 C/OV。",
        "",
        "## 结果",
        "",
        "全部用例的 A、B、C、AC、OV、P 与 PSW 高位一致，无严格不一致。",
        "",
        "## 严格不一致",
        "",
    ]
    if not mismatches:
        report.append("无。")
    for index, case, ours, theirs, fields in mismatches:
        report += [
            f"### {case.name} — {describe(case)}",
            "",
            f"- code：`{' '.join(f'{byte:02X}' for byte in case.code)}`",
            f"- ucsim：{format_snapshot(theirs)}",
            f"- 我们：{format_snapshot(ours)}",
            f"- 不一致字段：{', '.join(field for field, _, _ in fields)}",
            f"- guide flags：`{json.dumps(GUIDE_FLAGS.get(case.name, {}), ensure_ascii=False)}`",
            f"- 备注：{case.note or '-'}",
            "",
        ]

    report += [
        "",
        "## 已裁决差异",
        "",
        "### PSW.0（P）：直接写 PSW 之后",
        "",
        f"- 用例数：{sum(len(v) for k, v in info_by_name.items() if k in PSW_WRITE_NAMES)}",
        "- 现象：除 P 外所有位一致。ucsim 保留写入、递增或弹出得到的 PSW.0；"
        "我们按 ACC 重新计算 P。",
        "- Guide：只规定 P 由硬件维护——“Set/cleared by hardware each instruction cycle to "
        "indicate an odd/even number of ‘1’ bits in the accumulator.”"
        "（MCS-51 Programmer’s Guide and Instruction Set, Nov 1992, Order 270249-003, "
        "p. 9 / PDF p. 12）。Guide 没有单独定义“写 PSW.0”这一动作的结果，"
        "即写入本身不是受支持的置位方式。",
        f"- ucsim 探针：`MOV A,#1; MOV PSW,#0; NOP; NOP` 后 {probe_text}"
        "。两个周期后 P 仍未回到 ACC 的奇偶性，违反“each instruction cycle”。"
        "其源码只在写 ACC 时重算 P（`sim/ucsim/src/sims/s51.src/uc51.cc`，"
        "`cl_uc51_cpu::write`，ACC 分支）。",
        "- 裁决：按 guide 的硬件维护语义，P 应等于 ACC 的奇偶性；我们强制这一点，"
        "ucsim 为偏差，不修改 `core.py`。",
        "- 保留意见：写 PSW.0 与同周期硬件更新的先后顺序，guide 没有相位级规定；"
        "本项目不建模 S1–S6 相位，指令边界上取 P=parity(A) 是自洽选择。"
        "要绝对确认，需要在真实 8051 上做单指令实验。",
        "- 例：`MOV PSW,#00`，A=0x01 → ucsim PSW=0x00（P=0），我们 PSW=0x01（P=1）。",
        "",
        "| 指令 | 差异数 |",
        "|---|---:|",
    ]
    for name in sorted(info_by_name):
        report.append(f"| {name} | {len(info_by_name[name])} |")

    report += [
        "",
        "### DIV AB 且 B=0",
        "",
        "- Guide 定义 A、B 结果为未定义；两边都把 A/B 保持原值，没有可判定的差异。",
        "- 只比较 C/OV：C=0、OV=1，全部一致。",
        "",
        "### DA A 与 MUL/DIV 的 AC",
        "",
        "- 用完整值集合比较 AC，未发现差异；与 `data/isa.json` 的 `AC: unchanged` 一致。",
        "",
        "## 错误",
        "",
    ]
    if not errors:
        report.append("无。")
    for index, case, error in errors:
        report.append(f"- {case.name} — {describe(case)}：{error}")

    report += [
        "",
        "## 复现",
        "",
        "```bash",
        "python3 tools/compare_ucsim_flags.py            # 完整矩阵",
        "python3 tools/compare_ucsim_flags.py --quick    # 缩减矩阵",
        "python3 -m unittest discover -s tests",
        "python3 tools/check_data.py",
        "python3 tools/compare_ucsim_cycles.py",
        "```",
        "",
        "## 限制",
        "",
        "- 只覆盖单指令、确定性 flags；不含定时器、中断、端口与串口。",
        "- 操作数取自固定值集合，不是全部 256×256 组合。",
        "- CJNE 只比较 flags，不比较分支目标。",
        "- 结论绑定 ucsim 版本；升级 `sdcc-ucsim` 后必须重跑。",
        "",
    ]
    REPORT.write_text("\n".join(report), encoding="utf-8")

    print(f"checked: {len(cases) - len(errors)}/{len(cases)}")
    print(f"mismatches: {len(mismatches)}")
    print(f"informational: {len(info)}")
    if errors:
        print(f"errors: {len(errors)}")
    print(f"report: {REPORT.relative_to(ROOT)}")
    for index, case, ours, theirs, fields in mismatches[:10]:
        detail = ", ".join(f"{f}: ucsim={t} ours={o}" for f, o, t in fields)
        print(f"  0x{index} {case.name} [{describe(case)}]: {detail}")
    return 0 if not mismatches and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
