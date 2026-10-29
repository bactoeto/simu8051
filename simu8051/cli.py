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

from .session import Session

COMMON_SFRS = [
    "P0", "P1", "P2", "P3", "SP", "DPL", "DPH", "PSW", "ACC", "B",
    "IE", "IP", "TCON", "TMOD", "TH0", "TL0", "TH1", "TL1", "SCON", "PCON",
]

COMMANDS = (
    "run", "step", "cycle", "continue", "break", "delete",
    "regs", "sfr", "x", "w", "disassemble", "reset", "load",
    "display", "undisplay", "enable", "disable", "info",
    "help", "quit",
)
ALIASES = {
    "r": "run",
    "s": "step",
    "cy": "cycle",
    "c": "continue",
    "b": "break",
    "del": "delete",
    "clear": "delete",
    "d": "disassemble",
    "i": "info",
    "reg": "regs",
    "sf": "sfr",
    "res": "reset",
    "l": "load",
    "dp": "display",
    "und": "undisplay",
    "en": "enable",
    "on": "enable",
    "off": "disable",
    "h": "help",
    "?": "help",
    "q": "quit",
    "exit": "quit",
}
def canonical(word: str) -> str:
    """Resolve a command or subcommand alias to its canonical name."""
    return ALIASES.get(word.lower(), word.lower())


REG_NAMES = Session.register_names
SFR_NAMES = Session.sfr_names
SPACES = ("i", "x", "c")
DISPLAY_KINDS = ("regs", "reg", "sfr", "mem", "bit")
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
        return [name for name in (*COMMANDS, *ALIASES) if name.startswith(text)]
    head = ALIASES.get(tokens[0].lower(), tokens[0].lower())
    if head == "load" and len(tokens) == 1:
        return path_candidates(text)
    if head == "sfr" and len(tokens) == 1:
        return [name for name in SFR_NAMES if name.lower().startswith(text.lower())]
    if head in {"x", "w"} and len(tokens) == 1:
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
        return [name for name in ("cycle", "cy") if name.startswith(text.lower())]
    if head == "info" and len(tokens) == 1:
        return [name for name in (*INFO_KINDS, "b", "dp") if name.startswith(text.lower())]
    if head == "reset" and len(tokens) == 1:
        return [name for name in ("cold", "c") if name.startswith(text.lower())]
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


def prompt_text() -> str:
    """Return the interactive prompt, coloured only on a terminal."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None:
        return "8051> "
    colour = "\033[32m8051>\033[0m "
    if _readline is None:
        return colour
    #  and  tell readline which bytes do not occupy screen columns.
    return "\001\033[32m\0028051>\001\033[0m\002 "


HELP_TEXT = """\
Commands\n\n  Command      Aliases  Arguments            Description\n  ------------------------------------------------------\n\n  Execution\n  run          r        [N]                  run N instructions; omit N to run until a stop\n  step         s        [N]                  execute N instructions (default 1)\n  cycle        cy       [N]                  execute N machine cycles (default 1)\n  continue     c                             resume after a stop\n  reset        res      [cold]               reset; "cold" also clears IRAM and XDATA\n\n  Breakpoints\n  break        b        ADDR                 stop at PC ADDR, e.g. 0x0030\n  break cycle           N                    stop at machine cycle N\n  delete       del                           clear all breakpoints\n  info         i        break|display        list breakpoints or displays\n\n  Inspection\n  regs         reg                           PC, ACC, B, PSW, SP, DPTR, cycle\n  sfr          sf       [NAME]               all common SFRs, or one by name\n  x                     SPACE ADDR [N]       dump N bytes, 8 per line: i, x, c\n  w                     SPACE ADDR VALUE...  write bytes: i, x, c (c patches ROM)\n  disassemble  d        [ADDR] [N]           disassemble N instructions (default 8)\n  display      dp       SPEC                 add a persistent display\n  undisplay    und      N                    remove display item N\n  enable       on       N                    turn display item N on\n  disable      off      N                    turn display item N off\n\n  Other\n  load         l        FILE                 load another HEX file\n  help         h, ?                          show this text\n  quit         q, exit                       leave the simulator\n\n  Numbers   decimal or 0x/0o/0b prefix: 48  0x30  0o60  0b110000\n  Spaces    i = internal RAM | x = external RAM | c = ROM\n  Display   regs | reg NAME | sfr NAME | mem SPACE ADDR [N] | bit ADDR [N]\n  Step      current instruction printed after every stop\n  Keys      Tab complete | Up/Down history | Ctrl-L clear | Ctrl-C cancel | Ctrl-D quit\n"""


def parse_int(text: str) -> int:
    return int(text, 0)


def _table(headers: list[list[str]], values: list[list[str]]) -> str:
    """Render stacked header/value strips as one box-drawing table."""
    cells = [row for pair in zip(headers, values) for row in pair]
    columns = max(len(row) for row in cells)
    widths = [
        max((len(row[c]) for row in cells if c < len(row)), default=0)
        for c in range(columns)
    ]

    def line(row: list[str]) -> str:
        padded = [
            f" {row[c] if c < len(row) else '':<{widths[c]}} "
            for c in range(columns)
        ]
        return "│" + "│".join(padded) + "│"

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (width + 2) for width in widths) + right

    lines = [rule("┌", "┬", "┐")]
    for index, (header, value) in enumerate(zip(headers, values)):
        lines += [line(header), line(value)]
        lines.append(
            rule("└", "┴", "┘")
            if index == len(headers) - 1
            else rule("├", "┼", "┤")
        )
    return "\n".join(lines)


def format_regs(session: Session) -> str:
    psw = session.get_sfr("PSW")
    headers = [
        ["PC", "ACC", "B", "PSW", "SP", "DPTR", "cycle"],
        [f"R{i}" for i in range(8)],
        ["CY", "AC", "F0", "RS1", "RS0", "OV", "F1", "P"],
        ["TH0", "TL0", "TH1", "TL1", "TMOD", "TCON"],
    ]
    values = [
        [
            f"0x{session.pc:04X}",
            f"0x{session.get_reg('A'):02X}",
            f"0x{session.get_reg('B'):02X}",
            f"0x{psw:02X}",
            f"0x{session.get_sfr('SP'):02X}",
            f"0x{session.get_reg('DPTR'):04X}",
            str(session.cycle),
        ],
        [f"0x{session.get_reg(f'R{i}'):02X}" for i in range(8)],
        [str((psw >> bit) & 1) for bit in range(7, -1, -1)],
        [
            f"0x{session.get_sfr('TH0'):02X}",
            f"0x{session.get_sfr('TL0'):02X}",
            f"0x{session.get_sfr('TH1'):02X}",
            f"0x{session.get_sfr('TL1'):02X}",
            f"0x{session.get_sfr('TMOD'):02X}",
            f"0x{session.get_sfr('TCON'):02X}",
        ],
    ]
    return _table(headers, values)


def format_sfr(session: Session, name: str) -> str:
    try:
        value = session.get_sfr(name)
    except KeyError:
        return f"{name}=<unknown>"
    return f"{name}=0x{value:02X}"


def format_mem(session: Session, space: str, address: int, count: int) -> str:
    count = ((count + 7) // 8) * 8
    digits = 4 if space in {"x", "c"} else 2
    mask = 0xFFFF if digits == 4 else 0xFF
    lines = []
    for offset in range(0, count, 8):
        row = [
            f"{_read_space(session, space, (address + offset + i) & mask):02X}"
            for i in range(min(8, count - offset))
        ]
        lines.append(f"0x{(address + offset) & mask:0{digits}X}: " + " ".join(row))
    return "\n".join(lines)


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


def display_value(session: Session, item: tuple) -> str:
    kind = item[0]
    if kind == "regs":
        return format_regs(session)
    if kind == "reg":
        return f"{item[1]}=0x{session.get_reg(item[1]):02X}"
    if kind == "sfr":
        return format_sfr(session, item[1])
    if kind == "mem":
        return format_mem(session, item[1], item[2], item[3])
    if kind == "bit":
        return format_mem(session, "bit", item[1], item[2])
    return ""


def print_displays(session: Session, displays: list[dict]) -> None:
    for index, item in enumerate(displays, 1):
        if not item["enabled"]:
            continue
        try:
            value = display_value(session, item["spec"])
        except Exception as exc:
            value = f"<display error: {exc}>"
        prefix = f"[{index}] {item['label']}:"
        if "\n" in value:
            print(prefix)
            print(value)
        else:
            print(f"{prefix} {value}")


def parse_display(args: list[str]) -> tuple:
    if not args:
        raise ValueError("usage: display regs | reg NAME | sfr NAME | mem SPACE ADDR [N] | bit ADDR [N]")
    kind = args[0].lower()
    if kind == "regs" and len(args) == 1:
        return ("regs",)
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

    def current_step(self) -> str:
        text = self.session.format(self.session.decode())
        return f"PC=0x{self.session.pc:04X}  {text}"

    def stop_display(self, result) -> None:
        if result.reason is not None:
            print(result.reason)
        print(self.current_step())
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
        result = self.session.run(max_instructions=max_instructions)
        self.stop_display(result)

    def cmd_continue(self) -> None:
        result = self.session.run()
        self.stop_display(result)

    def cmd_break(self, args: list[str]) -> None:
        if not args:
            raise ValueError("usage: break ADDR | break cycle N")
        if canonical(args[0]) == "cycle":
            if len(args) < 2:
                raise ValueError("usage: break cycle N")
            cycle = parse_int(args[1])
            self.session.break_at_cycle(cycle)
            print(f"breakpoint at cycle {cycle}")
        else:
            addr = parse_int(args[0])
            self.session.break_at(addr)
            print(f"breakpoint at 0x{addr:04X}")

    def cmd_info(self, args: list[str]) -> None:
        what = canonical(args[0]) if args else "break"
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
        if not args:
            raise ValueError("usage: undisplay N")
        index = parse_int(args[0])
        if not 1 <= index <= len(self.displays):
            raise ValueError("bad display index")
        self.displays.pop(index - 1)

    def cmd_toggle(self, args: list[str], enabled: bool) -> None:
        if not args:
            raise ValueError("usage: enable N" if enabled else "usage: disable N")
        index = parse_int(args[0])
        if not 1 <= index <= len(self.displays):
            raise ValueError("bad display index")
        self.displays[index - 1]["enabled"] = enabled

    def cmd_sfr(self, args: list[str]) -> None:
        names = [args[0].upper()] if args else COMMON_SFRS
        print(" ".join(format_sfr(self.session, name) for name in names))

    def cmd_x(self, args: list[str]) -> None:
        if len(args) < 2 or args[0].lower() not in SPACES:
            raise ValueError("usage: x i|x|c ADDR [N]")
        space, address = args[0].lower(), parse_int(args[1])
        count = parse_int(args[2]) if len(args) > 2 else 16
        print(format_mem(self.session, space, address, count))

    def cmd_w(self, args: list[str]) -> None:
        if len(args) < 3 or args[0].lower() not in SPACES:
            raise ValueError("usage: w i|x|c ADDR VALUE...")
        space, address = args[0].lower(), parse_int(args[1])
        values = [parse_int(text) for text in args[2:]]
        for value in values:
            if not 0 <= value <= 0xFF:
                raise ValueError(f"value out of range: {value}")
        if space == "i" and not 0 <= address <= 0x7F:
            raise ValueError("internal RAM is 0x00-0x7F")
        if space == "i" and address + len(values) > 0x80:
            raise ValueError("internal RAM is 0x00-0x7F")
        target = {"i": "iram", "x": "xdata", "c": "code"}[space]
        for offset, value in enumerate(values):
            self.session.write(target, address + offset, value)
        print(format_mem(self.session, space, address, len(values)))

    def cmd_dis(self, args: list[str]) -> None:
        address = parse_int(args[0]) if args else self.session.pc
        count = parse_int(args[1]) if len(args) > 1 else 8
        for inst in self.session.disassemble(address, count):
            print(f"{inst.address:04X}  {inst.raw.hex():<6}   {self.session.format(inst)}")

    def cmd_reset(self, args: list[str]) -> None:
        word = args[0].lower() if args else ""
        self.session.reset(cold=word == "c" or canonical(word) == "cold")
        print("reset")
        print(self.current_step())
        print_displays(self.session, self.displays)

    def cmd_load(self, args: list[str]) -> None:
        if not args:
            raise ValueError("usage: load FILE")
        self.session.load(args[0])
        print(f"loaded {args[0]}")
        print(self.current_step())

    def dispatch(self, line: str) -> None:
        parts = shlex.split(line)
        if not parts:
            return
        cmd, args = parts[0].lower(), parts[1:]
        cmd = canonical(cmd)
        if cmd == "quit":
            self.quit = True
        elif cmd == "help":
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
        elif cmd == "delete":
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
        elif cmd == "w":
            self.cmd_w(args)
        elif cmd == "disassemble":
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
        else:
            print(f"unknown command: {cmd}")

    def loop(self) -> int:
        install_readline()
        print(HELP_TEXT)
        while not self.quit:
            try:
                line = input(prompt_text())
            except KeyboardInterrupt:
                print()
                continue
            except EOFError:
                print()
                break
            print()
            try:
                self.dispatch(line)
            except Exception as exc:
                print(f"error: {exc}", file=sys.stderr)
            print()
        stop = self.session.last_stop
        return 1 if stop is not None and stop.kind == "trap" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="8051 cycle-level simulator")
    ap.add_argument("hexfile")
    ap.add_argument("--plugin", action="append", default=[])
    ap.add_argument("--break", dest="breaks", action="append", default=[])
    ap.add_argument("--max-cycles", type=int)
    ap.add_argument("--run", action="store_true", help="run without REPL")
    ap.add_argument("--batch", action="store_true", help="alias for --run")
    ap.add_argument("--display", action="append", default=[])
    ap.add_argument("--no-default-display", action="store_true")
    args = ap.parse_args(argv)

    try:
        session = Session.open(args.hexfile)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        for spec in args.breaks:
            if spec.lower().startswith("cycle:"):
                session.break_at_cycle(parse_int(spec.split(":", 1)[1]))
            else:
                session.break_at(parse_int(spec))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

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
            session.load_plugin(path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cli = CLI(session, displays)
    if args.run or args.batch:
        result = session.run(max_cycles=args.max_cycles)
        cli.stop_display(result)
        return 1 if result.reason.kind == "trap" else 0

    print(f"loaded {args.hexfile}")
    print(cli.current_step())
    return cli.loop()


if __name__ == "__main__":
    sys.exit(main())
