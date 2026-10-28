"""Session/runner: wires hex image, core and plugins together.

A "step" executes one complete instruction. step_cycle() advances one
machine cycle. Plugin callbacks run after the core tick, before the next
cycle, so their writes take effect on the next machine cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .core import Core, Event, Trap
from .hex import Image, decode_at, format_instruction
from .plugin import Plugin, PluginContext, ScheduledCallback


@dataclass
class StepResult:
    pc: int
    next_pc: int
    cycles: int
    reason: str = ""
    instruction: str = ""
    halted: bool = False
    idle: bool = False


@dataclass
class Session:
    core: Core
    image: Optional[Image] = None
    plugins: list[Plugin] = field(default_factory=list)
    breakpoints_pc: set[int] = field(default_factory=set)
    breakpoints_cycle: set[int] = field(default_factory=set)
    stop_reason: str = ""
    trace: bool = False
    trace_fn: Optional[Callable[[str], None]] = None
    _scheduled: list[tuple[int, Callable]] = field(default_factory=list)
    _last_stop_pc: Optional[int] = None
    _stopped: bool = False
    _started: bool = False
    _stop_dispatched: bool = False

    def __post_init__(self):
        if self.image is not None:
            self.core.load_image(self.image)

    def start(self) -> None:
        if self._started:
            return
        for plugin, ctx in self._contexts():
            plugin.on_start(ctx)
        self._started = True

    # ------------------------------------------------------------------
    # plugin/session API
    # ------------------------------------------------------------------
    def schedule_at(self, cycle: int, callback: ScheduledCallback) -> None:
        if cycle < self.core.cycle:
            raise ValueError("cannot schedule in the past")
        self._scheduled.append((cycle, callback))

    def stop(self, reason: str = "plugin stop") -> None:
        self.stop_reason = reason or "plugin stop"
        self._stopped = True

    def break_at(self, address: int) -> None:
        self.breakpoints_pc.add(address & 0xFFFF)

    def break_at_cycle(self, cycle: int) -> None:
        self.breakpoints_cycle.add(cycle)

    def clear_breakpoints(self) -> None:
        self.breakpoints_pc.clear()
        self.breakpoints_cycle.clear()

    # ------------------------------------------------------------------
    # plugin dispatch
    # ------------------------------------------------------------------
    def _contexts(self):
        for plugin in self.plugins:
            yield plugin, PluginContext(self, plugin)

    def _dispatch_cycle(self) -> None:
        for plugin, ctx in self._contexts():
            plugin.on_cycle(ctx)

    def _dispatch_instruction(self, text: str) -> None:
        for plugin, ctx in self._contexts():
            plugin.on_instruction(ctx, text)

    def _dispatch_interrupt(self, event: Event) -> None:
        for plugin, ctx in self._contexts():
            plugin.on_interrupt(ctx, str(event.detail))

    def _run_scheduled(self) -> None:
        while True:
            due = [(c, cb) for c, cb in self._scheduled if c <= self.core.cycle]
            if not due:
                return
            due.sort(key=lambda item: item[0])
            self._scheduled = [(c, cb) for c, cb in self._scheduled if c > self.core.cycle]
            for _, callback in due:
                callback(PluginContext(self))

    def _handle_events(self, events: tuple[Event, ...]) -> None:
        for event in events:
            if event.kind == "interrupt":
                self._dispatch_interrupt(event)

    def _dispatch_stop(self) -> None:
        if self._stop_dispatched:
            return
        for plugin, ctx in self._contexts():
            plugin.on_stop(ctx, self.stop_reason)
        self._stop_dispatched = True

    def _check_cycle_break(self) -> bool:
        if self.core.cycle in self.breakpoints_cycle:
            self.stop_reason = f"cycle breakpoint {self.core.cycle}"
            self._stopped = True
            return True
        return False

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def step_cycle(self) -> StepResult:
        if self._stopped:
            return StepResult(self.core.pc, self.core.pc, 0, self.stop_reason, halted=self.core.halted)
        start_pc = self.core.pc
        start_cycle = self.core.cycle
        try:
            events = self.core.tick_cycle()
        except Trap as exc:
            self.stop_reason = str(exc)
            self._stopped = True
            self._dispatch_stop()
            return StepResult(start_pc, self.core.pc, self.core.cycle - start_cycle, self.stop_reason)
        self._handle_events(events)
        self._run_scheduled()
        self._dispatch_cycle()
        self._check_cycle_break()
        return StepResult(
            start_pc,
            self.core.pc,
            self.core.cycle - start_cycle,
            self.stop_reason,
            halted=self.core.halted,
            idle=self.core.idle,
        )

    def step(self) -> StepResult:
        if self._stopped:
            return StepResult(self.core.pc, self.core.pc, 0, self.stop_reason, halted=self.core.halted)
        if self.core.halted:
            self.stop_reason = "halted (power-down)"
            self._stopped = True
            return StepResult(self.core.pc, self.core.pc, 0, self.stop_reason, halted=True)
        if self.core.idle:
            return self.step_cycle()

        start_pc = self.core.pc
        if start_pc in self.breakpoints_pc and self._last_stop_pc != start_pc:
            self.stop_reason = f"pc breakpoint 0x{start_pc:04X}"
            self._last_stop_pc = start_pc
            self._stopped = True
            return StepResult(start_pc, start_pc, 0, self.stop_reason)

        text = format_instruction(decode_at(self.image, start_pc)) if self.image else ""
        if self.trace and self.trace_fn:
            self.trace_fn(f"{self.core.cycle:8d} {start_pc:04X} {text}")
        start_cycle = self.core.cycle
        try:
            while True:
                events = self.core.tick_cycle()
                self._handle_events(events)
                self._run_scheduled()
                self._dispatch_cycle()
                if self._check_cycle_break():
                    break
                if self.core._at_boundary() and not self.core.idle:
                    break
                if self.core.idle and self.core._vector is None:
                    break
                if self.core.halted:
                    break
        except Trap as exc:
            self.stop_reason = str(exc)
            self._stopped = True
            self._dispatch_stop()
        self._last_stop_pc = None
        self._dispatch_instruction(text)
        return StepResult(
            start_pc,
            self.core.pc,
            self.core.cycle - start_cycle,
            self.stop_reason,
            instruction=text,
            halted=self.core.halted,
            idle=self.core.idle,
        )

    def run(self, max_cycles: Optional[int] = None, max_instructions: Optional[int] = None) -> StepResult:
        start_cycle = self.core.cycle
        count = 0
        result = StepResult(self.core.pc, self.core.pc, 0)
        while not self._stopped:
            if max_cycles is not None and self.core.cycle - start_cycle >= max_cycles:
                self.stop_reason = f"max cycles {max_cycles}"
                break
            if max_instructions is not None and count >= max_instructions:
                self.stop_reason = f"max instructions {max_instructions}"
                break
            result = self.step()
            count += 1
            if result.reason:
                break
            if self.core.pc in self.breakpoints_pc and self.core.pc != result.pc:
                # step() will stop before executing the breakpoint next time.
                break
        if self.stop_reason:
            self._dispatch_stop()
        return result

    def reset(self, cold: bool = False) -> None:
        self.core.reset(cold=cold)
        self._scheduled.clear()
        self.stop_reason = ""
        self._stopped = False
        self._stop_dispatched = False
        self._last_stop_pc = None
