#!/usr/bin/env python3
"""Interactive CLI for the 8051 simulator."""
from __future__ import annotations

import argparse
import atexit
import glob
import os
import shlex
import sys
from pathlib import Path

try:
    import readline as _readline
except ImportError:  # Windows without pyreadline3
    _readline = None

from . import hex as hexmod
from .core import Core
from .mem_table import SFR
from .plugin import load_plugin
from .step import Session

COMMON_SFRS = [
    "P0", "P1", "P2", "P3", "SP", "DPL", "DPH", "PSW", "ACC", "B",
    "IE", "IP", "TCON", "TMOD", "TH0", "TL0", "TH1", "TL1", "SCON", "PCON",
]

COMMANDS = (
    "run", "step", "cycle", "continue", "break", "delete",
    "regs", "sfr", "x", "dis", "reset", "load",
    "display", "undisplay", "enable", "disable", "info",
    "trace", "help", "quit", "exit", "q",
)
REG_NAMES = ("A", "B", "PSW", "SP", "DPL", "DPH", "DPTR", "PC", "R0", "R1",
             "R2", "R3", "R4", "R5", "R6", "R7")
SFR_NAMES = tuple(sorted(SFR))
SPACES = ("code", "iram", "sfr", "direct", "xdata", "bit")
DISPLAY_KINDS = ("regs", "pc", "reg", "sfr", "mem", "bit")
INFO_KINDS = ("break", "display")


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
        return [name for name in COMMANDS if name.startswith(text)]
    head = tokens[0].lower()
    if head == "load" and len(tokens) == 1:
        return path_candidates(text)
    if head == "sfr" and len(tokens) == 1:
        return [name for name in SFR_NAMES if name.lower().startswith(text.lower())]
    if head == "x" and len(tokens) == 1:
        return [name for name in SPACES if name.startswith(text.lower())]
    if head == "display":
        if len(tokens) == 1:
            return [name for name in DISPLAY_KINDS if name.startswith(text.lower())]
        kind = tokens[1].lower()
        if kind == "mem" and len(tokens) == 2:
            return [name for name in SPACES if name.startswith(text.lower())]
        if kind == "reg" and len(tokens) == 2:
            return [name for name in REG_NAMES if name.lower().startswith(text.lower())]
        if kind == "sfr" and len(tokens) == 2:
            return [name for name in SFR_NAMES if name.lower().startswith(text.lower())]
    if head == "break" and len(tokens) == 1:
        return ["cycle"] if "cycle".startswith(text.lower()) else []
    if head == "info" and len(tokens) == 1:
        return [name for name in INFO_KINDS if name.startswith(text.lower())]
    if head == "trace" and len(tokens) == 1:
        return [name for name in ("on", "off") if name.startswith(text.lower())]
    if head == "reset" and len(tokens) == 1:
        return ["cold"] if "cold".startswith(text.lower()) else []
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
    # GNU readline does not bind Tab to completion by default (unlike bash).
    _readline.parse_and_bind("tab: complete")
    atexit.register(_save_history)


def _save_history() -> None:
    if _readline is None:
        return
    try:
        _readline.write_history_file(str(history_path()))
    except OSError:
        pass


HELP_TEXT = """\
Commands

  Execution
    run [N]                run N instructions; omit N to run until a stop
    step [N]               execute N instructions (default 1)
    cycle [N]              execute N machine cycles (default 1)
    continue               resume after a stop
    reset [cold]           reset; "cold" also clears IRAM and XDATA

  Breakpoints
    break ADDR             stop at PC ADDR, e.g. 0x0030
    break cycle N          stop at machine cycle N
    delete                 clear all breakpoints
    info break             list breakpoints

  Inspection
    regs                   PC, ACC, B, PSW, SP, DPTR, cycle
    sfr [NAME]             all common SFRs, or one by name
    x SPACE ADDR [N]       dump N bytes (default 16)
    dis [ADDR] [N]         disassemble N instructions (default 8)
    display SPEC           add a persistent display
    undisplay N            remove display item N
    enable N               turn display item N on
    disable N              turn display item N off
    info display           list display items

  Other
    load FILE              load another HEX file
    trace on|off           print each executed instruction
    help, ?                show this text
    quit, exit, q          leave the simulator

  Numbers   decimal or 0x/0o/0b prefix: 48  0x30  0o60  0b110000
  Spaces    code | iram | sfr | direct | xdata | bit
  Display   regs | pc | reg NAME | sfr NAME | mem SPACE ADDR [N] | bit ADDR [N]
  Keys      Tab complete | Up/Down history | Ctrl-L clear | Ctrl-C cancel | Ctrl-D quit
"""


def parse_int(text: str) -> int:
    return int(text, 0)


def format_regs(session: Session) -> str:
    core = session.core
    psw = core.get_sfr("PSW")
    regs = [
        f"PC=0x{core.pc:04X}", f"ACC=0x{core.get_reg('A'):02X}",
        f"B=0x{core.get_reg('B'):02X}", f"PSW=0x{psw:02X}",
        f"SP=0x{core.get_sfr('SP'):02X}", f"DPTR=0x{core.get_reg('DPTR'):04X}",
        f"cycle={core.cycle}",
    ]
    for i in range(8):
        regs.append(f"R{i}=0x{core.get_reg(f'R{i}'):02X}")
    return " ".join(regs)


def format_sfr(session: Session, name: str) -> str:
    core = session.core
    try:
        value = core.get_sfr(name)
    except KeyError:
        return f"{name}=<unknown>"
    return f"{name}=0x{value:02X}"


def format_mem(session: Session, space: str, address: int, count: int) -> str:
    core = session.core
    values = []
    for i in range(count):
        addr = (address + i) & 0xFFFF if space in {"code", "xdata"} else (address + i) & 0xFF
        values.append(f"{_read_space(core, space, addr):02X}")
    return f"{space}[0x{address:04X}..]: " + " ".join(values)


def _read_space(core: Core, space: str, address: int) -> int:
    if space == "code":
        return core.read_code(address)
    if space == "iram":
        return core.read_indirect(address)
    if space in {"sfr", "direct"}:
        return core.read_latch(address)
    if space == "xdata":
        return core.read_xdata(address)
    if space == "bit":
        return core.read_bit(address)
    raise ValueError(f"unknown space {space!r}")


def display_value(session: Session, item: tuple) -> str:
    kind = item[0]
    if kind == "regs":
        return format_regs(session)
    if kind == "reg":
        return f"{item[1]}=0x{session.core.get_reg(item[1]):02X}"
    if kind == "sfr":
        return format_sfr(session, item[1])
    if kind == "mem":
        return format_mem(session, item[1], item[2], item[3])
    if kind == "bit":
        return format_mem(session, "bit", item[1], item[2])
    if kind == "pc":
        text = ""
        if session.image:
            inst = hexmod.decode_at(session.image, session.core.pc)
            text = hexmod.format_instruction(inst)
        return f"PC=0x{session.core.pc:04X}  {text}".rstrip()
    return ""


def print_displays(session: Session, displays: list[dict]) -> None:
    for index, item in enumerate(displays, 1):
        if not item["enabled"]:
            continue
        try:
            value = display_value(session, item["spec"])
        except Exception as exc:
            value = f"<display error: {exc}>"
        print(f"[{index}] {item['label']}: {value}")


def parse_display(args: list[str]) -> tuple:
    if not args:
        raise ValueError("usage: display regs | reg NAME | sfr NAME | mem SPACE ADDR [N] | bit ADDR [N] | pc")
    kind = args[0].lower()
    if kind == "regs" and len(args) == 1:
        return ("regs",)
    if kind == "pc" and len(args) == 1:
        return ("pc",)
    if kind == "reg" and len(args) == 2:
        return ("reg", args[1].upper())
    if kind == "sfr" and len(args) == 2:
        return ("sfr", args[1].upper())
    if kind == "mem" and len(args) in (3, 4):
        return ("mem", args[1].lower(), parse_int(args[2]), parse_int(args[3]) if len(args) == 4 else 1)
    if kind == "bit" and len(args) in (2, 3):
        return ("bit", parse_int(args[1]), parse_int(args[2]) if len(args) == 3 else 1)
    raise ValueError("bad display specification")


def display_label(spec: tuple) -> str:
    if spec[0] == "regs":
        return "regs"
    if spec[0] == "reg":
        return f"reg {spec[1]}"
    if spec[0] == "sfr":
        return f"sfr {spec[1]}"
    if spec[0] == "mem":
        return f"mem {spec[1]} 0x{spec[2]:X} {spec[3]}"
    if spec[0] == "bit":
        return f"bit 0x{spec[1]:X} {spec[2]}"
    return spec[0]


class CLI:
    def __init__(self, session: Session, displays: list[dict]):
        self.session = session
        self.displays = displays
        self.quit = False

    def stop_display(self, result) -> None:
        if result.reason:
            print(result.reason)
        print_displays(self.session, self.displays)

    def cmd_help(self) -> None:
        print(HELP_TEXT)

    def cmd_step(self, args: list[str]) -> None:
        count = parse_int(args[0]) if args else 1
        result = None
        for _ in range(count):
            result = self.session.step()
            if result.reason:
                break
        if result:
            self.stop_display(result)

    def cmd_cycle(self, args: list[str]) -> None:
        count = parse_int(args[0]) if args else 1
        result = None
        for _ in range(count):
            result = self.session.step_cycle()
            if result.reason:
                break
        if result:
            self.stop_display(result)

    def cmd_run(self, args: list[str]) -> None:
        max_instructions = parse_int(args[0]) if args else None
        self.session.resume()
        result = self.session.run(max_instructions=max_instructions)
        self.stop_display(result)

    def cmd_continue(self) -> None:
        self.session.resume()
        result = self.session.run()
        self.stop_display(result)

    def cmd_break(self, args: list[str]) -> None:
        if not args:
            raise ValueError("usage: break ADDR | break cycle N")
        if args[0].lower() == "cycle":
            cycle = parse_int(args[1])
            self.session.break_at_cycle(cycle)
            print(f"breakpoint at cycle {cycle}")
        else:
            addr = parse_int(args[0])
            self.session.break_at(addr)
            print(f"breakpoint at 0x{addr:04X}")

    def cmd_info(self, args: list[str]) -> None:
        what = args[0].lower() if args else "break"
        if what == "break":
            print("pc:", " ".join(f"0x{x:04X}" for x in sorted(self.session.breakpoints_pc)) or "(none)")
            print("cycle:", " ".join(str(x) for x in sorted(self.session.breakpoints_cycle)) or "(none)")
        elif what == "display":
            for i, item in enumerate(self.displays, 1):
                state = "on" if item["enabled"] else "off"
                print(f"{i}: [{state}] {item['label']}")
        else:
            raise ValueError("info break | info display")

    def cmd_display(self, args: list[str]) -> None:
        spec = parse_display(args)
        for item in self.displays:
            if item["spec"] == spec:
                print(f"already registered as {self.displays.index(item) + 1}")
                return
        self.displays.append({"spec": spec, "label": display_label(spec), "enabled": True})
        print(f"display {len(self.displays)}: {self.displays[-1]['label']}")

    def cmd_undisplay(self, args: list[str]) -> None:
        index = parse_int(args[0])
        if not 1 <= index <= len(self.displays):
            raise ValueError("bad display index")
        self.displays.pop(index - 1)

    def cmd_toggle(self, args: list[str], enabled: bool) -> None:
        index = parse_int(args[0])
        if not 1 <= index <= len(self.displays):
            raise ValueError("bad display index")
        self.displays[index - 1]["enabled"] = enabled

    def cmd_sfr(self, args: list[str]) -> None:
        names = [args[0].upper()] if args else COMMON_SFRS
        print(" ".join(format_sfr(self.session, name) for name in names))

    def cmd_x(self, args: list[str]) -> None:
        if len(args) < 2:
            raise ValueError("usage: x SPACE ADDR [N]")
        space, address = args[0].lower(), parse_int(args[1])
        count = parse_int(args[2]) if len(args) > 2 else 16
        print(format_mem(self.session, space, address, count))

    def cmd_dis(self, args: list[str]) -> None:
        if not self.session.image:
            print("no image")
            return
        address = parse_int(args[0]) if args else self.session.core.pc
        count = parse_int(args[1]) if len(args) > 1 else 8
        insts = hexmod.disassemble(self.session.image, address, min(0x10000, address + count * 3))
        for inst in insts[:count]:
            print(f"{inst.address:04X}  {inst.raw.hex():<6} {hexmod.format_instruction(inst)}")

    def cmd_reset(self, args: list[str]) -> None:
        self.session.reset(cold=bool(args and args[0].lower() == "cold"))
        print("reset")
        print_displays(self.session, self.displays)

    def cmd_load(self, args: list[str]) -> None:
        if not args:
            raise ValueError("usage: load FILE")
        image = hexmod.load_hex(args[0])
        self.session.image = image
        self.session.core.load_image(image)
        self.session.reset(cold=True)
        print(f"loaded {args[0]}")

    def dispatch(self, line: str) -> None:
        parts = shlex.split(line)
        if not parts:
            return
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in {"quit", "exit", "q"}:
            self.quit = True
        elif cmd in {"help", "?"}:
            self.cmd_help()
        elif cmd == "step":
            self.cmd_step(args)
        elif cmd == "cycle":
            self.cmd_cycle(args)
        elif cmd == "run":
            self.cmd_run(args)
        elif cmd == "continue":
            self.cmd_continue()
        elif cmd == "break":
            self.cmd_break(args)
        elif cmd in {"delete", "clear"}:
            self.session.clear_breakpoints()
            print("breakpoints cleared")
        elif cmd == "info":
            self.cmd_info(args)
        elif cmd == "regs":
            print(format_regs(self.session))
        elif cmd == "sfr":
            self.cmd_sfr(args)
        elif cmd == "x":
            self.cmd_x(args)
        elif cmd == "dis":
            self.cmd_dis(args)
        elif cmd == "reset":
            self.cmd_reset(args)
        elif cmd == "load":
            self.cmd_load(args)
        elif cmd == "display":
            self.cmd_display(args)
        elif cmd == "undisplay":
            self.cmd_undisplay(args)
        elif cmd == "enable":
            self.cmd_toggle(args, True)
        elif cmd == "disable":
            self.cmd_toggle(args, False)
        elif cmd == "trace":
            self.session.trace = bool(args and args[0].lower() in {"on", "1", "true"})
            print("trace", "on" if self.session.trace else "off")
        else:
            print(f"unknown command: {cmd}")

    def loop(self) -> int:
        install_readline()
        print(HELP_TEXT)
        while not self.quit:
            try:
                line = input("8051> ")
            except KeyboardInterrupt:
                print()
                continue
            except EOFError:
                print()
                break
            try:
                self.dispatch(line)
            except Exception as exc:
                print(f"error: {exc}", file=sys.stderr)
        return 1 if self.session.stop_reason and "reserved" in self.session.stop_reason else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="8051 cycle-level simulator")
    ap.add_argument("hexfile")
    ap.add_argument("--plugin", action="append", default=[])
    ap.add_argument("--break", dest="breaks", action="append", default=[])
    ap.add_argument("--max-cycles", type=int)
    ap.add_argument("--run", action="store_true", help="run without REPL")
    ap.add_argument("--batch", action="store_true", help="alias for --run")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--display", action="append", default=[])
    ap.add_argument("--no-default-display", action="store_true")
    args = ap.parse_args(argv)

    try:
        image = hexmod.load_hex(args.hexfile)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    core = Core()
    core.load_image(image)
    core.reset(cold=True)
    session = Session(core=core, image=image)
    session.trace = args.trace
    session.trace_fn = lambda text: print(text)

    for spec in args.breaks:
        if spec.lower().startswith("cycle:"):
            session.break_at_cycle(parse_int(spec.split(":", 1)[1]))
        else:
            session.break_at(parse_int(spec))

    displays: list[dict] = []
    if not args.no_default_display:
        displays.append({"spec": ("regs",), "label": "regs", "enabled": True})
    for text in args.display:
        try:
            spec = parse_display(shlex.split(text))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        displays.append({"spec": spec, "label": display_label(spec), "enabled": True})

    try:
        for path in args.plugin:
            session.plugins.append(load_plugin(path, session))
        session.start()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cli = CLI(session, displays)
    if args.run or args.batch:
        result = session.run(max_cycles=args.max_cycles)
        cli.stop_display(result)
        return 1 if result.reason and ("trap" in result.reason or "reserved" in result.reason) else 0

    print(f"loaded {args.hexfile}: 0x{image.min_addr or 0:04X}-0x{image.max_addr or 0:04X}")
    return cli.loop()


if __name__ == "__main__":
    sys.exit(main())
