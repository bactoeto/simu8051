"""Typed plugin template.

A plugin is a file with hook functions. All of them are optional:

    setup(ctx), on_start(ctx), on_cycle(ctx),
    on_instruction(ctx, text), on_interrupt(ctx, name), on_stop(ctx, reason)

Copy this file and change the cycle numbers, addresses and values.

Run:
    python3 -m simu8051 firmware.hex --plugin examples/plugin_template.py

The import is for editor/type-checker support. The simulator creates the
PluginContext object and passes it to setup().
"""
from simu8051.plugin import PluginContext


def at_cycle_100(ctx: PluginContext) -> None:
    # Write 0x5A to internal RAM address 0x20.
    ctx.write("iram", 0x20, 0x5A)
    print("cycle", ctx.cycle, "PC", hex(ctx.pc), "RAM[0x20] = 0x5A")


def setup(ctx: PluginContext) -> None:
    # schedule_at(N, f) calls f(ctx) after machine cycle N.
    ctx.schedule_at(100, at_cycle_100)


def on_cycle(ctx: PluginContext) -> None:
    # Called after every machine cycle.
    if ctx.cycle == 50:
        print("cycle 50, PC", hex(ctx.pc))
