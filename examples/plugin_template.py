"""Typed plugin template.

A plugin is a file with module-level functions. Available hooks:

    setup(ctx), on_cycle(ctx), on_instruction(ctx, text),
    on_interrupt(ctx, name), on_stop(ctx, reason)

Run:
    python3 -m simu8051 firmware.hex --plugin examples/plugin_template.py
"""
from simu8051.session import PluginContext


def on_cycle(ctx: PluginContext) -> None:
    # Called after every machine cycle.
    if ctx.cycle == 100:
        ctx.write("iram", 0x20, 0x5A)
        print("cycle", ctx.cycle, "PC", hex(ctx.pc), "RAM[0x20] = 0x5A")
