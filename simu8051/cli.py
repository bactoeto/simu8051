#!/usr/bin/env python3
"""Interactive CLI for the 8051 simulator.

This module is a consumer of simu8051.session. It owns presentation only.
"""
from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass

from .cli_format import (
    COMMON_SFRS,
    REG_HEADERS,
    _table,
    disassembly_lines,
    format_mem,
    mem_lines,
    reg_values,
    render_lines,
    sfr_lines,
)
from .cli_shell import (
    DISPLAY_COMMANDS,
    SPACES,
    canonical,
    install_readline,
    prompt_text,
)
from .session import Session

HELP_SECTIONS = (
    (
        "Execution",
        (
            ("run", "r", "[N]", "run N instructions or until a stop"),
            ("step", "s", "[N]", "execute N instructions (default 1)"),
            ("cycle", "cy", "[N]", "execute N machine cycles (default 1)"),
            ("continue", "c", "", "continue after a stop"),
            ("reset", "res", "[cold]", "reset; cold also clears IRAM/XDATA"),
        ),
    ),
    (
        "Breakpoints",
        (
            ("break", "b", "ADDR", "stop at PC ADDR"),
            ("break cycle", "", "N", "stop at machine cycle N"),
            ("delete", "del", "", "clear breakpoints"),
            ("info", "i", "break|display", "list breakpoints or displays"),
        ),
    ),
    (
        "Inspection",
        (
            ("regs", "reg", "", "common registers"),
            ("sfr", "sf", "[NAME]", "all SFRs, or one by name"),
            ("x", "", "SPACE ADDR [N]", "dump; 8 per row, default 2 rows"),
            ("w", "", "SPACE ADDR VALUE...", "write i, x or c"),
            ("disassemble", "d", "[N] [ADDR]", "disassemble N instructions"),
            ("display", "dp", "COMMAND...", "watch a command after execution and state changes"),
            ("undisplay", "und", "N", "remove a display item"),
            ("enable", "on", "N", "enable a display item"),
            ("disable", "off", "N", "disable a display item"),
        ),
    ),
    (
        "Other",
        (
            ("load", "l", "FILE", "load another HEX file"),
            ("help", "h, ?", "", "show this text"),
            ("quit", "q, exit", "", "leave the simulator"),
        ),
    ),
)

HELP_NOTES = (
    ("Numbers", "decimal or 0x/0o/0b prefix"),
    ("Spaces", "i = internal RAM | x = external RAM | c = ROM"),
    ("Display", "sfr [NAME] | x SPACE ADDR [N] | disassemble [N] [ADDR]"),
    ("Output", "current instruction and registers after every stop"),
    ("Keys", "Tab complete | Up/Down history | Ctrl-L clear | Ctrl-C cancel | Ctrl-D quit"),
)


def _format_help() -> str:
    rows = [row for _, section in HELP_SECTIONS for row in section]
    command_width = max(19, len("Command"), *(len(row[0]) for row in rows))
    alias_width = max(len("Aliases"), *(len(row[1]) for row in rows))
    argument_width = max(len("Arguments"), *(len(row[2]) for row in rows))
    header = (
        f"  {'Command':<{command_width}}  {'Aliases':<{alias_width}}  "
        f"{'Arguments':<{argument_width}}  Description"
    )
    lines = ["Commands", "", header, "  " + "-" * (len(header) - 2)]
    for title, section in HELP_SECTIONS:
        lines += ["", f"  {title}"]
        for command, alias, arguments, description in section:
            lines.append(
                f"  {command:<{command_width}}  {alias:<{alias_width}}  "
                f"{arguments:<{argument_width}}  {description}"
            )
    note_width = max(8, *(len(label) for label, _ in HELP_NOTES))
    lines.append("")
    for label, text in HELP_NOTES:
        lines.append(f"  {label:<{note_width}}  {text}")
    return "\n".join(line.rstrip() for line in lines) + "\n"


HELP_TEXT = _format_help()

DISPLAY_TRIGGERS = frozenset({
    "step", "cycle", "run", "continue",
    "reset", "load",
    "display", "enable",
})


def parse_int(text: str) -> int:
    return int(text, 0)


def command_name(line: str) -> str | None:
    parts = shlex.split(line)
    return canonical(parts[0]) if parts else None


def parse_display(args: list[str]) -> list[str]:
    if not args:
        raise ValueError("usage: display COMMAND...")
    head = canonical(args[0])
    if head not in DISPLAY_COMMANDS:
        raise ValueError("display: use sfr, x or disassemble")
    if head == "sfr":
        if len(args) > 2:
            raise ValueError("usage: display sfr [NAME]")
    elif head == "x":
        if len(args) < 3 or len(args) > 4 or args[1].lower() not in SPACES:
            raise ValueError("usage: display x i|x|c ADDR [N]")
        for text in args[2:]:
            parse_int(text)
    else:
        if len(args) > 3:
            raise ValueError("usage: display disassemble [N] [ADDR]")
        for text in args[1:]:
            parse_int(text)
    return [head, *args[1:]]


@dataclass
class DisplayItem:
    command: list[str]
    label: str
    enabled: bool = True
    previous: list[list[str]] | None = None


class CLI:
    def __init__(self, session: Session, displays: list[DisplayItem]):
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
            item.previous = None

    def after_stop(self, reason) -> None:
        if reason is not None:
            print(reason)
        print(self.current_step())
        print(self.registers())

    def display_text(self, item: DisplayItem) -> str:
        command = item.command
        head, args = command[0], command[1:]
        if head == "sfr":
            lines = sfr_lines(self.session, [args[0].upper()] if args else COMMON_SFRS)
        elif head == "x":
            lines = self.x_lines(args)
        elif head == "disassemble":
            lines = self.disassemble_lines(args)
        else:
            raise ValueError(f"unsupported display: {head}")
        text = render_lines(lines, item.previous)
        item.previous = lines
        return text

    def print_displays(self) -> None:
        for index, item in enumerate(self.displays, 1):
            if not item.enabled:
                continue
            try:
                text = self.display_text(item)
            except Exception as exc:
                text = f"<display error: {exc}>"
            text = text.rstrip("\n")
            prefix = f"[{index}] {item.label}:"
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
                f"{i}: [{'on' if item.enabled else 'off'}] {item.label}"
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
        before = mem_lines(self.session, space, address, len(values))
        for offset, value in enumerate(values):
            self.session.write(target, address + offset, value)
        return format_mem(self.session, space, address, len(values), before)

    def disassemble_lines(self, args: list[str]) -> list[list[str]]:
        count = parse_int(args[0]) if args else 8
        address = parse_int(args[1]) if len(args) > 1 else self.session.pc
        return disassembly_lines(self.session, address, count)

    def cmd_disassemble(self, args: list[str]) -> str:
        return render_lines(self.disassemble_lines(args))

    def cmd_display(self, args: list[str]) -> str:
        command = parse_display(args)
        for item in self.displays:
            if item.command == command:
                return f"already registered as {self.displays.index(item) + 1}"
        label = " ".join(args)
        self.displays.append(DisplayItem(command, label))
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
        self.displays[index - 1].enabled = enabled
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
            name = None
            try:
                name = command_name(line)
                text = self.run_command(line)
                if text:
                    print(text)
            except Exception as exc:
                print(f"error: {exc}", file=sys.stderr)
            if name in DISPLAY_TRIGGERS and not self.quit:
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

    displays: list[DisplayItem] = []
    for text in args.display:
        try:
            command = parse_display(shlex.split(text))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        displays.append(DisplayItem(command, text))

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
