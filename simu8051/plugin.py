"""Plugin API for cycle-level stimuli.

A plugin is a plain Python file with optional hook functions:

    setup(ctx), on_cycle(ctx), on_instruction(ctx, text),
    on_interrupt(ctx, name), on_stop(ctx, reason)

It uses ``ctx`` to read/write memory, drive pins, schedule callbacks by
machine cycle, or stop the run.

For editor/type-checker support, annotate the context parameter::

    from simu8051.session import PluginContext

    def setup(ctx: PluginContext) -> None:
        ctx.write("iram", 0x20, 0x5A)
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional, TypeAlias

if TYPE_CHECKING:
    from .session import Session

Space: TypeAlias = Literal["code", "iram", "sfr", "direct", "xdata", "bit"]


class Plugin:
    """Base class for plugin adapters. Every hook is optional."""

    def setup(self, ctx: PluginContext) -> None:
        """Called once when the plugin is loaded."""

    def on_cycle(self, ctx: PluginContext) -> None:
        """Called after every machine cycle."""

    def on_instruction(self, ctx: PluginContext, text: str) -> None:
        """Called after one instruction has completed."""

    def on_interrupt(self, ctx: PluginContext, name: str) -> None:
        """Called when an interrupt is accepted."""

    def on_stop(self, ctx: PluginContext, reason: str) -> None:
        """Called when the run stops."""


class PluginContext:
    """Handle passed to a plugin by the simulator.

    It is an instance of this class, created by the simulator; plugins do not
    construct it themselves.
    """

    def __init__(self, session: "Session"):
        self._session = session

    @property
    def cycle(self) -> int:
        """Current machine cycle number."""
        return self._session.cycle

    @property
    def pc(self) -> int:
        """Current program counter."""
        return self._session.pc

    def read(self, space: Space, address: int) -> int:
        """Read one byte from a memory space.

        ``iram``: internal RAM; ``sfr``: SFR latch; ``direct``: direct
        address (ports read the pin); ``xdata``: external RAM; ``code``:
        program memory; ``bit``: bit address.
        """
        return self._session.read(space, address)

    def write(self, space: Space, address: int, value: int) -> None:
        """Write one byte to a memory space (see :meth:`read`)."""
        self._session.write(space, address, value)

    def get_reg(self, name: str) -> int:
        """Read a register: A, B, PSW, SP, DPL, DPH, DPTR, PC, R0-R7."""
        return self._session.get_reg(name)

    def set_reg(self, name: str, value: int) -> None:
        """Write a register: A, B, PSW, SP, DPL, DPH, DPTR, PC, R0-R7."""
        self._session.set_reg(name, value)

    def get_sfr(self, name_or_addr: str | int) -> int:
        """Read an SFR by name (``"P1"``) or address (``0x90``)."""
        return self._session.get_sfr(name_or_addr)

    def set_sfr(self, name_or_addr: str | int, value: int) -> None:
        """Write an SFR by name or address, including SFR side effects."""
        self._session.set_sfr(name_or_addr, value)

    def drive_pin(self, port: int, bit: int, value: int) -> None:
        """Drive one external pin. ``port`` 0..3 or 0x80/0x90/0xA0/0xB0."""
        self._session.drive_pin(port, bit, value)

    def release_pin(self, port: int, mask: int = 0xFF) -> None:
        """Release external drive for the selected pins."""
        self._session.release_pin(port, mask)

    def stop(self, reason: str = "plugin stop") -> None:
        """Request the session to stop."""
        self._session._request_stop(reason)


HOOKS = ("setup", "on_cycle", "on_instruction", "on_interrupt", "on_stop")


def _hook(module, name: str):
    fn = getattr(module, name, None)
    return fn if callable(fn) and not isinstance(fn, type) else None


def load_plugin(path: str | Path, session: "Session") -> Plugin:
    """Load a plugin file and return an initialized Plugin instance."""
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"simu8051_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not any(_hook(module, name) for name in HOOKS):
        raise ImportError(
            f"{path}: plugin must define setup(ctx) or a hook function "
            f"({', '.join(HOOKS)})"
        )
    plugin = _FunctionPlugin(module)
    plugin.setup(PluginContext(session))
    return plugin


class _FunctionPlugin(Plugin):
    """Adapter that dispatches hook calls to module-level functions."""

    def __init__(self, module):
        self._module = module

    def _call(self, name: str, *args) -> None:
        fn = _hook(self._module, name)
        if fn is not None:
            fn(*args)

    def setup(self, ctx: PluginContext) -> None:
        self._call("setup", ctx)

    def on_cycle(self, ctx: PluginContext) -> None:
        self._call("on_cycle", ctx)

    def on_instruction(self, ctx: PluginContext, text: str) -> None:
        self._call("on_instruction", ctx, text)

    def on_interrupt(self, ctx: PluginContext, name: str) -> None:
        self._call("on_interrupt", ctx, name)

    def on_stop(self, ctx: PluginContext, reason: str) -> None:
        self._call("on_stop", ctx, reason)
