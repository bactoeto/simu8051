#!/usr/bin/env python3
"""Interactive CLI for the 8051 simulator.

This module is a consumer of simu8051.session. It owns presentation only.
"""
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
    "IE", "IP", "TCON", "TMOD", "TH0", "TL0", "TH1", "TL1", "SCON", "SBUF", "PCON",
]

COMMANDS = (
    "run", "step", "cycle", "continue", "break", "delete",
    "regs", "sfr", "x", "w", "disassemble", "reset", "load",
    "display", "undisplay", "enable", "disable", "info", "help", "quit",
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
DISPLAY_COMMANDS = ("sfr", "x", "disassemble")
SPACES = ("i", "x", "c")
INFO_KINDS = ("break", "display")
SFR_NAMES = Session.sfr_names


def canonical(word: str) -> str:
    """Resolve a command or subcommand alias to its canonical name."""
    return ALIASES.get(word.lower(), word.lower())


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
    head = canonical(tokens[0])
    if head == "load" and len(tokens) == 1:
        return path_candidates(text)
    if head == "sfr" and len(tokens) == 1:
        return [name for name in SFR_NAMES if name.lower().startswith(text.lower())]
    if head in {"x", "w"} and len(tokens) == 1:
        return [name for name in SPACES if name.startswith(text.lower())]
    if head == "display":
        if len(tokens) == 1:
            return [name for name in DISPLAY_COMMANDS if name.startswith(text.lower())]
        return completion_candidates(tokens[1:], text)
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
    return "\001\033[32m\0028051>\001\033[0m\002 "


HELP_TEXT = """\
Commands

  Command              Aliases  Arguments            Description
  --------------------------------------------------------------

  Execution
  run                  r        [N]                  run N instructions or until a stop
  step                 s        [N]                  execute N instructions (default 1)
  cycle                cy       [N]                  execute N machine cycles (default 1)
  continue             c                             continue after a stop
  reset                res      [cold]               reset; cold also clears IRAM/XDATA

  Breakpoints
  break                b        ADDR                 stop at PC ADDR
  break cycle          N                             stop at machine cycle N
  delete               del                           clear breakpoints
  info                 i        break|display        list breakpoints or displays

  Inspection
  regs                 reg                           common registers
  sfr                  sf       [NAME]               all SFRs, or one by name
  x                    SPACE ADDR [N]               dump; 8 per row, default 2 rows
  w                    SPACE ADDR VALUE...          write i, x or c
  disassemble          d        [N] [ADDR]           disassemble N instructions
  display              dp       COMMAND...           run a command after every command
  undisplay            und      N                    remove a display item
  enable               on       N                    enable a display item
  disable              off      N                    disable a display item

  Other
  load                 l        FILE                 load another HEX file
  help                 h, ?                          show this text
  quit                 q, exit                       leave the simulator

  Numbers   decimal or 0x/0o/0b prefix
  Spaces    i = internal RAM | x = external RAM | c = ROM
  Display   sfr [NAME] | x SPACE ADDR [N] | disassemble [N] [ADDR]
  Output    current instruction and registers after every stop
  Keys      Tab complete | Up/Down history | Ctrl-L clear | Ctrl-C cancel | Ctrl-D quit
"""


def parse_int(text: str) -> int:
    return int(text, 0)


BLUE = "\033[94m"
RESET = "\033[0m"

REG_HEADERS = [
    ["PC", "ACC", "B", "PSW", "SP", "DPTR"],
    [f"R{i}" for i in range(8)],
    ["CY", "AC", "F0", "RS1", "RS0", "OV", "F1", "P"],
    ["T0", "T1", "TMOD", "TCON", "cycle"],
]


def use_color() -> bool:
    """Colour only on a terminal, and never when NO_COLOR is present."""
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text: str, changed: bool, colour: bool | None = None) -> str:
    """Wrap changed text in blue, leaving the width of the plain text intact."""
    if not changed:
        return text
    if colour is None:
        colour = use_color()
    return f"{BLUE}{text}{RESET}" if colour else text


def _table(
    headers: list[list[str]],
    values: list[list[str]],
    previous: list[list[str]] | None = None,
    colour: bool | None = None,
) -> str:
    """Render stacked header/value strips as one box-drawing table."""
    cells = [row for pair in zip(headers, values) for row in pair]
    columns = max(len(row) for row in cells)
    widths = [
        max((len(row[c]) for row in cells if c < len(row)), default=0)
        for c in range(columns)
    ]

    def line(row: list[str], old: list[str] | None) -> str:
        padded = []
        for c in range(columns):
            text = row[c] if c < len(row) else ""
            changed = old is not None and c < len(old) and old[c] != text
            field = paint(text, changed, colour)
            padded.append(f" {field}{' ' * (widths[c] - len(text))} ")
        return "│" + "│".join(padded) + "│"

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (width + 2) for width in widths) + right

    lines = [rule("┌", "┬", "┐")]
    for index, (header, value) in enumerate(zip(headers, values)):
        old = previous[index] if previous is not None and index < len(previous) else None
        lines += [line(header, None), line(value, old)]
        lines.append(
            rule("└", "┴", "┘")
            if index == len(headers) - 1
            else rule("├", "┼", "┤")
        )
    return "\n".join(lines)


def reg_values(session: Session) -> list[list[str]]:
    psw = session.get_sfr("PSW")
    t0 = (session.get_sfr("TH0") << 8) | session.get_sfr("TL0")
    t1 = (session.get_sfr("TH1") << 8) | session.get_sfr("TL1")
    return [
        [
            f"0x{session.pc:04X}",
            f"0x{session.get_reg('A'):02X}",
            f"0x{session.get_reg('B'):02X}",
            f"0x{psw:02X}",
            f"0x{session.get_sfr('SP'):02X}",
            f"0x{session.get_reg('DPTR'):04X}",
        ],
        [f"0x{session.get_reg(f'R{i}'):02X}" for i in range(8)],
        [str((psw >> bit) & 1) for bit in range(7, -1, -1)],
        [
            f"0x{t0:04X}",
            f"0x{t1:04X}",
            f"0x{session.get_sfr('TMOD'):02X}",
            f"0x{session.get_sfr('TCON'):02X}",
            str(session.cycle),
        ],
    ]


def render_lines(
    lines: list[list[str]],
    previous: list[list[str]] | None = None,
    colour: bool | None = None,
    separator: str = " ",
) -> str:
    """Join fields into lines, colouring fields that differ from the snapshot."""
    out = []
    for row, fields in enumerate(lines):
        old = previous[row] if previous is not None and row < len(previous) else None
        parts = []
        for column, text in enumerate(fields):
            changed = old is not None and column < len(old) and old[column] != text
            parts.append(paint(text, changed, colour))
        out.append(separator.join(parts))
    return "\n".join(out)


def format_sfr(session: Session, name: str) -> str:
    try:
        value = session.get_sfr(name)
    except KeyError:
        return f"{name}=<unknown>"
    return f"{name}=0x{value:02X}"


def sfr_lines(session: Session, names: list[str]) -> list[list[str]]:
    return [[format_sfr(session, name) for name in names]]


def mem_lines(
    session: Session,
    space: str,
    address: int,
    count: int | None = None,
) -> list[list[str]]:
    start = address & ~7
    if count is None:
        stop = start + 16
    else:
        stop = max((address + count + 7) & ~7, start + 8)
    digits = 4 if space in {"x", "c"} else 2
    mask = 0xFFFF if digits == 4 else 0xFF
    lines = []
    for row_start in range(start, stop, 8):
        row = [f"0x{row_start & mask:0{digits}X}:"]
        row += [
            f"{_read_space(session, space, (row_start + i) & mask):02X}"
            for i in range(8)
        ]
        lines.append(row)
    return lines


def disassembly_lines(session: Session, address: int, count: int) -> list[list[str]]:
    return [
        [f"{inst.address:04X}  {inst.raw.hex():<6}   {session.format(inst)}"]
        for inst in session.disassemble(address, count)
    ]


def format_mem(
    session: Session,
    space: str,
    address: int,
    count: int | None = None,
) -> str:
    return render_lines(mem_lines(session, space, address, count))


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


def parse_display(args: list[str]) -> list[str]:
    if not args:
        raise ValueError("usage: display COMMAND...")
    head = canonical(args[0])
    if head not in DISPLAY_COMMANDS:
        raise ValueError("display: use sfr, x or disassemble")
    return [head, *args[1:]]


class CLI:
    def __init__(self, session: Session, displays: list[dict]):
        self.session = session
        self.displays = displays
        self.quit = False
        self.regs_previous: list[list[str]] | None = None

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------
    def current_step(self) -> str:
        text = self.session.format(self.session.decode())
        return f"PC=0x{self.session.pc:04X}  {text}"

    def registers(self) -> str:
        values = reg_values(self.session)
        text = _table(REG_HEADERS, values, self.regs_previous)
        self.regs_previous = values
        return text

    def reset_baselines(self) -> None:
        self.regs_previous = None
        for item in self.displays:
            item["previous"] = None

    def after_stop(self, reason) -> None:
        if reason is not None:
            print(reason)
        print(self.current_step())
        print(self.registers())

    def display_text(self, item: dict) -> str:
        command = item["command"]
        head, args = command[0], command[1:]
        if head == "sfr":
            lines = sfr_lines(self.session, [args[0].upper()] if args else COMMON_SFRS)
        elif head == "x":
            lines = self.x_lines(args)
        elif head == "disassemble":
            lines = self.disassemble_lines(args)
        else:
            raise ValueError(f"unsupported display: {head}")
        text = render_lines(lines, item.get("previous"))
        item["previous"] = lines
        return text

    def print_displays(self) -> None:
        for index, item in enumerate(self.displays, 1):
            if not item["enabled"]:
                continue
            try:
                text = self.display_text(item)
            except Exception as exc:
                text = f"<display error: {exc}>"
            text = text.rstrip("\n")
            prefix = f"[{index}] {item['label']}:"
            if "\n" in text:
                print(prefix)
                print(text)
            elif text:
                print(f"{prefix} {text}")
            else:
                print(prefix)

    def after_command(self) -> None:
        self.print_displays()

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def run_command(self, line: str) -> str | None:
        parts = shlex.split(line)
        if not parts:
            return None
        command = canonical(parts[0])
        handler = getattr(self, "cmd_" + command, None)
        if handler is None:
            raise ValueError(f"unknown command: {command}")
        return handler(parts[1:])

    def cmd_quit(self, args: list[str]) -> None:
        self.quit = True
        return None

    def cmd_help(self, args: list[str]) -> str:
        return HELP_TEXT

    def cmd_step(self, args: list[str]) -> None:
        count = parse_int(args[0]) if args else 1
        result = None
        for _ in range(count):
            result = self.session.step()
            if result.reason:
                break
        if result:
            self.after_stop(result.reason)
        return None

    def cmd_cycle(self, args: list[str]) -> None:
        count = parse_int(args[0]) if args else 1
        result = None
        for _ in range(count):
            result = self.session.step_cycle()
            if result.reason:
                break
        if result:
            self.after_stop(result.reason)
        return None

    def cmd_run(self, args: list[str]) -> None:
        limit = parse_int(args[0]) if args else None
        result = self.session.run(max_instructions=limit)
        self.after_stop(result.reason)
        return None

    def cmd_continue(self, args: list[str]) -> None:
        result = self.session.run()
        self.after_stop(result.reason)
        return None

    def cmd_reset(self, args: list[str]) -> None:
        word = args[0].lower() if args else ""
        self.session.reset(cold=word == "c" or canonical(word) == "cold")
        self.reset_baselines()
        print("reset")
        print(self.current_step())
        print(self.registers())
        return None

    def cmd_load(self, args: list[str]) -> str:
        if not args:
            raise ValueError("usage: load FILE")
        self.session.load(args[0])
        self.reset_baselines()
        return f"loaded {args[0]}\n{self.current_step()}\n{self.registers()}"

    def cmd_break(self, args: list[str]) -> str:
        if not args:
            raise ValueError("usage: break ADDR | break cycle N")
        if canonical(args[0]) == "cycle":
            if len(args) < 2:
                raise ValueError("usage: break cycle N")
            cycle = parse_int(args[1])
            self.session.break_at_cycle(cycle)
            return f"breakpoint at cycle {cycle}"
        address = parse_int(args[0])
        self.session.break_at(address)
        return f"breakpoint at 0x{address:04X}"

    def cmd_delete(self, args: list[str]) -> str:
        self.session.clear_breakpoints()
        return "breakpoints cleared"

    def cmd_info(self, args: list[str]) -> str:
        what = canonical(args[0]) if args else "break"
        if what == "break":
            pc = " ".join(f"0x{x:04X}" for x in sorted(self.session.breakpoints_pc))
            cyc = " ".join(str(x) for x in sorted(self.session.breakpoints_cycle))
            return f"pc: {pc or '(none)'}\ncycle: {cyc or '(none)'}"
        if what == "display":
            return "\n".join(
                f"{i}: [{'on' if item['enabled'] else 'off'}] {item['label']}"
                for i, item in enumerate(self.displays, 1)
            )
        raise ValueError("info break | info display")

    def cmd_regs(self, args: list[str]) -> str:
        return self.registers()

    def cmd_sfr(self, args: list[str]) -> str:
        names = [args[0].upper()] if args else COMMON_SFRS
        return render_lines(sfr_lines(self.session, names))

    def x_lines(self, args: list[str]) -> list[list[str]]:
        if len(args) < 2 or args[0].lower() not in SPACES:
            raise ValueError("usage: x i|x|c ADDR [N]")
        space, address = args[0].lower(), parse_int(args[1])
        count = parse_int(args[2]) if len(args) > 2 else None
        return mem_lines(self.session, space, address, count)

    def cmd_x(self, args: list[str]) -> str:
        return render_lines(self.x_lines(args))

    def cmd_w(self, args: list[str]) -> str:
        if len(args) < 3 or args[0].lower() not in SPACES:
            raise ValueError("usage: w i|x|c ADDR VALUE...")
        space, address = args[0].lower(), parse_int(args[1])
        values = [parse_int(text) for text in args[2:]]
        if any(not 0 <= value <= 0xFF for value in values):
            raise ValueError("value out of range")
        if space == "i" and not 0 <= address <= 0x7F:
            raise ValueError("internal RAM is 0x00-0x7F")
        if space == "i" and address + len(values) > 0x80:
            raise ValueError("internal RAM is 0x00-0x7F")
        target = {"i": "iram", "x": "xdata", "c": "code"}[space]
        for offset, value in enumerate(values):
            self.session.write(target, address + offset, value)
        return format_mem(self.session, space, address, len(values))

    def disassemble_lines(self, args: list[str]) -> list[list[str]]:
        count = parse_int(args[0]) if args else 8
        address = parse_int(args[1]) if len(args) > 1 else self.session.pc
        return disassembly_lines(self.session, address, count)

    def cmd_disassemble(self, args: list[str]) -> str:
        return render_lines(self.disassemble_lines(args))

    def cmd_display(self, args: list[str]) -> str:
        command = parse_display(args)
        for item in self.displays:
            if item["command"] == command:
                return f"already registered as {self.displays.index(item) + 1}"
        label = " ".join(args)
        self.displays.append(
            {"command": command, "label": label, "enabled": True, "previous": None}
        )
        return f"display {len(self.displays)}: {label}"

    def cmd_undisplay(self, args: list[str]) -> None:
        if not args:
            raise ValueError("usage: undisplay N")
        index = parse_int(args[0])
        if not 1 <= index <= len(self.displays):
            raise ValueError("bad display index")
        self.displays.pop(index - 1)
        return None

    def cmd_toggle(self, args: list[str], enabled: bool) -> None:
        if not args:
            raise ValueError("usage: enable N" if enabled else "usage: disable N")
        index = parse_int(args[0])
        if not 1 <= index <= len(self.displays):
            raise ValueError("bad display index")
        self.displays[index - 1]["enabled"] = enabled
        return None

    def cmd_enable(self, args: list[str]) -> None:
        return self.cmd_toggle(args, True)

    def cmd_disable(self, args: list[str]) -> None:
        return self.cmd_toggle(args, False)

    # ------------------------------------------------------------------
    # REPL
    # ------------------------------------------------------------------
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
            try:
                text = self.run_command(line)
                if text:
                    print(text)
            except Exception as exc:
                print(f"error: {exc}", file=sys.stderr)
            if line.strip() and not self.quit:
                self.after_command()
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
    for text in args.display:
        try:
            command = parse_display(shlex.split(text))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        displays.append(
            {"command": command, "label": text, "enabled": True, "previous": None}
        )

    try:
        for path in args.plugin:
            session.load_plugin(path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cli = CLI(session, displays)
    if args.run or args.batch:
        result = session.run(max_cycles=args.max_cycles)
        cli.after_stop(result.reason)
        cli.after_command()
        return 1 if result.reason.kind == "trap" else 0

    return cli.loop()


if __name__ == "__main__":
    sys.exit(main())
