"""The public interface of the simulator.

A Session is one loaded 8051 machine: program memory, CPU state, execution
control, breakpoints, memory/register access and plugin hosting. Everything
outside this module is an implementation detail.

The API is organised by machine capability, not by any particular front end.
The bundled CLI is only one consumer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .core import Core, Event, Trap
from .hex import (
    CODE_SIZE,
    HexError,
    Image,
    Instruction,
    decode_at,
    format_instruction,
    load_hex,
)
from .mem_table import SFR
from .plugin import Plugin, PluginContext, load_plugin as _load_plugin

_REGISTER_NAMES = (
    "A", "B", "PSW", "SP", "DPL", "DPH", "DPTR", "PC",
    "R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7",
)
_SFR_NAMES = tuple(sorted(SFR))

StopKind = Literal[
    "pc_breakpoint",
    "cycle_breakpoint",
    "max_cycles",
    "max_instructions",
    "halt",
    "trap",
    "plugin",
]


@dataclass(frozen=True)
class StopReason:
    """Why the machine stopped. ``kind`` is for programs, ``str()`` for people."""

    kind: StopKind
    detail: str = ""
    pc: int | None = None
    cycle: int | None = None

    def __str__(self) -> str:
        if self.kind == "pc_breakpoint":
            return f"pc breakpoint 0x{(self.pc or 0):04X}"
        if self.kind == "cycle_breakpoint":
            return f"cycle breakpoint {self.cycle}"
        if self.kind == "max_cycles":
            return f"max cycles {self.detail}"
        if self.kind == "max_instructions":
            return f"max instructions {self.detail}"
        if self.kind == "halt":
            return "halted (power-down)"
        if self.kind == "trap":
            return self.detail
        return self.detail or "plugin stop"


@dataclass(frozen=True)
class StepResult:
    """Result of one step() or step_cycle() call."""

    pc: int
    next_pc: int
    cycles: int
    reason: StopReason | None = None
    instruction: Instruction | None = None
    completed: bool = False
    halted: bool = False
    idle: bool = False


@dataclass(frozen=True)
class RunResult:
    """Summary of one run() call."""

    reason: StopReason
    instructions: int
    cycles: int
    start_pc: int
    end_pc: int


class Session:
    register_names: tuple[str, ...] = _REGISTER_NAMES
    sfr_names: tuple[str, ...] = _SFR_NAMES

    def __init__(
        self,
        core: Core,
        entry: int = 0,
        loaded: bytearray | None = None,
    ) -> None:
        self._core = core
        self._entry = entry & 0xFFFF
        # Decoder view over the CPU's program memory; no second copy.
        self._program = Image(code=core.code)
        self._loaded = loaded if loaded is not None else bytearray(CODE_SIZE // 8)
        self._plugins: list[Plugin] = []
        self._pc_breaks: set[int] = set()
        self._cycle_breaks: set[int] = set()
        self._trace: Callable[[str], None] | None = None
        self._pending: StopReason | None = None
        self._last_reason: StopReason | None = None
        self._skip_pc_break: int | None = None
        self._stop_dispatched = False

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    @classmethod
    def open(cls, path: str | Path, cold: bool = True) -> "Session":
        """Load a HEX file and start at its start address, or address 0."""
        image = load_hex(path)
        core = Core()
        core.load_image(image)
        session = cls(core, entry=image.start or 0, loaded=image.loaded)
        session.reset(cold=cold)
        return session

    @classmethod
    def from_bytes(
        cls,
        code: bytes | bytearray,
        entry: int = 0,
        cold: bool = True,
    ) -> "Session":
        """Build a session from raw program bytes loaded at address 0."""
        core = Core(bytes(code))
        loaded = bytearray(CODE_SIZE // 8)
        for address in range(min(len(code), CODE_SIZE)):
            loaded[address >> 3] |= 1 << (address & 7)
        session = cls(core, entry=entry, loaded=loaded)
        session.reset(cold=cold)
        return session

    def load(self, path: str | Path, cold: bool = True) -> None:
        """Replace the program. A malformed file leaves the session unchanged."""
        image = load_hex(path)
        self._core.load_image(image)
        self._loaded = image.loaded
        self._entry = (image.start or 0) & 0xFFFF
        self.reset(cold=cold)

    def reset(self, cold: bool = False) -> None:
        """Reset the CPU and return the PC to the entry address."""
        self._core.reset(cold=cold)
        self._core.pc = self._entry
        self._pending = None
        self._last_reason = None
        self._skip_pc_break = None
        self._stop_dispatched = False

    # ------------------------------------------------------------------
    # state and metadata
    # ------------------------------------------------------------------
    @property
    def pc(self) -> int:
        """Current program counter."""
        return self._core.pc

    @property
    def cycle(self) -> int:
        """Machine cycles elapsed since reset."""
        return self._core.cycle

    @property
    def halted(self) -> bool:
        """True in power-down mode."""
        return self._core.halted

    @property
    def idle(self) -> bool:
        """True in idle mode."""
        return self._core.idle

    @property
    def last_stop(self) -> StopReason | None:
        """Reason for the current stop, or None after starting to run again."""
        return self._last_reason

    # ------------------------------------------------------------------
    # breakpoints
    # ------------------------------------------------------------------
    def break_at(self, address: int) -> None:
        """Stop before executing the instruction at address."""
        self._pc_breaks.add(address & 0xFFFF)

    def break_at_cycle(self, cycle: int) -> None:
        """Stop when the machine cycle counter reaches cycle."""
        self._cycle_breaks.add(cycle)

    def clear_breakpoints(self) -> None:
        """Remove all PC and cycle breakpoints."""
        self._pc_breaks.clear()
        self._cycle_breaks.clear()

    @property
    def breakpoints_pc(self) -> frozenset[int]:
        return frozenset(self._pc_breaks)

    @property
    def breakpoints_cycle(self) -> frozenset[int]:
        return frozenset(self._cycle_breaks)

    # ------------------------------------------------------------------
    # memory, registers, pins
    # ------------------------------------------------------------------
    def read(self, space: str, address: int) -> int:
        """Read one byte from code, iram, sfr, direct, xdata or bit."""
        key = space.lower()
        if key == "code":
            return self._core.read_code(address)
        if key == "iram":
            return self._core.read_indirect(address)
        if key == "sfr":
            return self._core.read_latch(address)
        if key == "direct":
            return self._core.read_direct(address)
        if key == "xdata":
            return self._core.read_xdata(address)
        if key == "bit":
            return self._core.read_bit(address)
        raise ValueError(f"unknown space {space!r}")

    def write(self, space: str, address: int, value: int) -> None:
        """Write one byte to code, iram, sfr, direct, xdata or bit."""
        key = space.lower()
        if key == "code":
            self._core.write_code(address, value)
            bit = address & 0xFFFF
            self._loaded[bit >> 3] |= 1 << (bit & 7)
        elif key == "iram":
            self._core.write_indirect(address, value)
        elif key in {"sfr", "direct"}:
            self._core.write_direct(address, value)
        elif key == "xdata":
            self._core.write_xdata(address, value)
        elif key == "bit":
            self._core.write_bit(address, value)
        else:
            raise ValueError(f"unknown space {space!r}")

    def get_reg(self, name: str) -> int:
        """Read: A, B, PSW, SP, DPL, DPH, DPTR, PC, R0-R7."""
        return self._core.get_reg(name)

    def set_reg(self, name: str, value: int) -> None:
        """Write: A, B, PSW, SP, DPL, DPH, DPTR, PC, R0-R7."""
        self._core.set_reg(name, value)

    def get_sfr(self, name_or_addr: str | int) -> int:
        """Read an SFR latch by name or address."""
        return self._core.get_sfr(name_or_addr)

    def set_sfr(self, name_or_addr: str | int, value: int) -> None:
        """Write an SFR by name or address, with SFR side effects."""
        self._core.set_sfr(name_or_addr, value)

    def drive_pin(self, port: int, bit: int, value: int) -> None:
        """Drive one external pin; the port latch is unchanged."""
        self._core.drive_pin(port, bit, value)

    def drive_port(self, port: int, mask: int, value: int) -> None:
        """Drive selected pins; bits outside mask keep their level."""
        self._core.drive_port(port, mask, value)

    def release_pin(self, port: int, mask: int = 0xFF) -> None:
        """Release external drive on the selected pins."""
        self._core.release_pin(port, mask)

    # ------------------------------------------------------------------
    # program inspection
    # ------------------------------------------------------------------
    def is_loaded(self, address: int) -> bool:
        """True when the HEX file (or a code write) supplied this address.

        This is loader coverage, not hardware state. An unwritten address and
        a byte explicitly loaded as 0xFF both read as 0xFF.
        """
        bit = address & 0xFFFF
        return bool(self._loaded[bit >> 3] & (1 << (bit & 7)))

    def decode(self, pc: int | None = None) -> Instruction:
        """Decode the instruction at pc, defaulting to the current PC."""
        return decode_at(self._program, self._core.pc if pc is None else pc)

    def disassemble(self, start: int | None = None, count: int = 8) -> list[Instruction]:
        """Decode count instructions from start, following linear addresses."""
        if count <= 0:
            return []
        pc = self._core.pc if start is None else start & 0xFFFF
        result: list[Instruction] = []
        for _ in range(count):
            instruction = decode_at(self._program, pc)
            result.append(instruction)
            pc = (pc + instruction.size) & 0xFFFF
        return result

    def format(self, instruction: Instruction) -> str:
        """Render an instruction, or ``"NULL"`` if its bytes were not loaded."""
        if not all(
            self.is_loaded(instruction.address + offset)
            for offset in range(instruction.size)
        ):
            return "NULL"
        return format_instruction(instruction)

    # ------------------------------------------------------------------
    # plugins and tracing
    # ------------------------------------------------------------------
    def add_plugin(self, plugin: Plugin) -> None:
        """Register a constructed plugin and call its setup hook once."""
        plugin.setup(PluginContext(self))
        self._plugins.append(plugin)

    def load_plugin(self, path: str | Path) -> Plugin:
        """Load a plugin file, call its setup hook once and register it."""
        plugin = _load_plugin(path, self)
        self._plugins.append(plugin)
        return plugin

    def set_trace(self, callback: Callable[[str], None] | None) -> None:
        """Emit one line per instruction to callback, or disable with None."""
        self._trace = callback

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def step(self) -> StepResult:
        """Execute one instruction. Interrupt vectoring counts as part of it."""
        self._start()
        return self._execute()

    def step_cycle(self) -> StepResult:
        """Advance exactly one machine cycle."""
        self._start()
        start_pc = self._core.pc
        start_cycle = self._core.cycle

        if self._core.halted:
            reason = StopReason("halt", pc=start_pc, cycle=start_cycle)
            self._stop(reason)
            return StepResult(start_pc, start_pc, 0, reason, halted=True)

        if self._pending is not None:
            reason = self._pending
            self._stop(reason)
            return StepResult(
                start_pc, start_pc, 0, reason,
                halted=self._core.halted, idle=self._core.idle,
            )

        instruction = decode_at(self._program, start_pc)
        at_boundary = self._core._at_boundary()
        was_op = self._core._op is not None
        was_idle = self._core.idle
        was_halted = self._core.halted
        was_vector = not was_op and not at_boundary
        if self._trace is not None and at_boundary and not was_idle:
            self._trace(f"{self._core.cycle:8d} {start_pc:04X} {self.format(instruction)}")

        try:
            events = self._core.tick_cycle()
        except Trap as exc:
            reason = StopReason(
                "trap", detail=str(exc), pc=self._core.pc, cycle=self._core.cycle
            )
            self._stop(reason)
            return StepResult(
                start_pc, self._core.pc, self._core.cycle - start_cycle, reason,
                halted=self._core.halted, idle=self._core.idle,
            )

        self._handle_events(events)
        self._dispatch_cycle()

        completed = self._retired(was_op, at_boundary, was_idle, was_halted, was_vector)
        reason = self._pending or self._cycle_break()
        if completed:
            self._dispatch_instruction(self.format(instruction))
            if reason is None and self._pending is not None:
                reason = self._pending
        if reason is not None:
            self._stop(reason)

        return StepResult(
            start_pc,
            self._core.pc,
            self._core.cycle - start_cycle,
            reason,
            instruction=instruction if completed else None,
            completed=completed,
            halted=self._core.halted,
            idle=self._core.idle,
        )

    def run(
        self,
        max_cycles: int | None = None,
        max_instructions: int | None = None,
    ) -> RunResult:
        """Run until a stop condition. Limits are counted from this call."""
        self._start()
        start_pc = self._core.pc
        start_cycle = self._core.cycle
        instructions = 0

        while True:
            if self._core.halted:
                self._stop(StopReason("halt", pc=self._core.pc, cycle=self._core.cycle))
                break
            if max_instructions is not None and instructions >= max_instructions:
                self._stop(StopReason(
                    "max_instructions", detail=str(max_instructions),
                    pc=self._core.pc, cycle=self._core.cycle,
                ))
                break
            used = self._core.cycle - start_cycle
            if max_cycles is not None and used >= max_cycles:
                self._stop(StopReason(
                    "max_cycles", detail=str(max_cycles),
                    pc=self._core.pc, cycle=self._core.cycle,
                ))
                break
            remaining = None if max_cycles is None else max_cycles - used
            result = self._execute(remaining, max_cycles)
            if result.completed:
                instructions += 1
            if result.reason is not None:
                break

        reason = self._last_reason
        if reason is None:
            reason = StopReason("halt", pc=self._core.pc, cycle=self._core.cycle)
            self._stop(reason)
        return RunResult(
            reason,
            instructions,
            self._core.cycle - start_cycle,
            start_pc,
            self._core.pc,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _start(self) -> None:
        last = self._last_reason
        if last is not None and last.kind == "pc_breakpoint":
            self._skip_pc_break = last.pc
        else:
            self._skip_pc_break = None
        self._last_reason = None
        self._pending = None
        self._stop_dispatched = False

    def _request_stop(self, reason: str = "plugin stop") -> None:
        self._pending = StopReason(
            "plugin",
            detail=reason or "plugin stop",
            pc=self._core.pc,
            cycle=self._core.cycle,
        )

    def _stop(self, reason: StopReason) -> None:
        self._last_reason = reason
        if not self._stop_dispatched:
            text = str(reason)
            for plugin, ctx in self._contexts():
                plugin.on_stop(ctx, text)
            self._stop_dispatched = True

    def _execute(self, budget: int | None = None, limit: int | None = None) -> StepResult:
        start_pc = self._core.pc
        start_cycle = self._core.cycle

        if self._core.halted:
            reason = StopReason("halt", pc=start_pc, cycle=start_cycle)
            self._stop(reason)
            return StepResult(start_pc, start_pc, 0, reason, halted=True)

        if self._pending is not None:
            reason = self._pending
            self._stop(reason)
            return StepResult(
                start_pc, start_pc, 0, reason,
                halted=self._core.halted, idle=self._core.idle,
            )

        if self._core._at_boundary() and self._pc_break_hit(start_pc):
            reason = StopReason("pc_breakpoint", pc=start_pc, cycle=self._core.cycle)
            self._stop(reason)
            return StepResult(
                start_pc, start_pc, 0, reason,
                halted=False, idle=self._core.idle,
            )

        instruction = decode_at(self._program, start_pc)
        text = self.format(instruction)
        if self._trace is not None and self._core._at_boundary() and not self._core.idle:
            self._trace(f"{self._core.cycle:8d} {start_pc:04X} {text}")

        completed = False
        reason: StopReason | None = None
        while True:
            if budget is not None and budget <= 0:
                reason = StopReason(
                    "max_cycles",
                    detail=str(limit if limit is not None else 0),
                    pc=self._core.pc,
                    cycle=self._core.cycle,
                )
                break

            at_boundary = self._core._at_boundary()
            was_op = self._core._op is not None
            was_idle = self._core.idle
            was_halted = self._core.halted
            was_vector = not was_op and not at_boundary

            try:
                events = self._core.tick_cycle()
            except Trap as exc:
                reason = StopReason(
                    "trap", detail=str(exc), pc=self._core.pc, cycle=self._core.cycle
                )
                break

            self._handle_events(events)
            self._dispatch_cycle()

            if not completed and self._retired(
                was_op, at_boundary, was_idle, was_halted, was_vector
            ):
                completed = True

            if self._pending is not None:
                reason = self._pending
                break

            cycle_reason = self._cycle_break()
            if cycle_reason is not None:
                reason = cycle_reason
                break
            if self._core._at_boundary():
                break
            if budget is not None:
                budget -= 1

        if completed:
            self._dispatch_instruction(text)
            if reason is None and self._pending is not None:
                reason = self._pending
        if reason is not None:
            self._stop(reason)

        return StepResult(
            start_pc,
            self._core.pc,
            self._core.cycle - start_cycle,
            reason,
            instruction=instruction if completed else None,
            completed=completed,
            halted=self._core.halted,
            idle=self._core.idle,
        )

    def _retired(
        self,
        was_op: bool,
        at_boundary: bool,
        was_idle: bool,
        was_halted: bool,
        was_vector: bool,
    ) -> bool:
        if self._core._op is not None:
            return False
        if was_op:
            return True
        return at_boundary and not was_idle and not was_halted and not was_vector

    def _pc_break_hit(self, pc: int) -> bool:
        if pc not in self._pc_breaks:
            self._skip_pc_break = None
            return False
        if self._skip_pc_break == pc:
            self._skip_pc_break = None
            return False
        self._skip_pc_break = None
        return True

    def _cycle_break(self) -> StopReason | None:
        if self._core.cycle in self._cycle_breaks:
            return StopReason(
                "cycle_breakpoint", pc=self._core.pc, cycle=self._core.cycle
            )
        return None

    # ------------------------------------------------------------------
    # plugin dispatch
    # ------------------------------------------------------------------
    def _contexts(self):
        for plugin in self._plugins:
            yield plugin, PluginContext(self)

    def _handle_events(self, events: tuple[Event, ...]) -> None:
        for event in events:
            if event.kind == "interrupt":
                for plugin, ctx in self._contexts():
                    plugin.on_interrupt(ctx, str(event.detail))

    def _dispatch_cycle(self) -> None:
        for plugin, ctx in self._contexts():
            plugin.on_cycle(ctx)

    def _dispatch_instruction(self, text: str) -> None:
        for plugin, ctx in self._contexts():
            plugin.on_instruction(ctx, text)


__all__ = [
    "Session",
    "StepResult",
    "RunResult",
    "StopReason",
    "Plugin",
    "PluginContext",
    "Instruction",
    "HexError",
]
