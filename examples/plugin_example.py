"""最简单的 8051 激励插件示例。

用法：
    python3 -m simu8051 firmware.hex --plugin examples/plugin_example.py

行为：
1. 第 20 个机器周期把 INT0 / P3.2 拉低；
2. 第 30 个机器周期释放 INT0；
3. 第 100 个机器周期把内部 RAM 0x20 写成 0x5A，并打印当前 PC。
"""
from simu8051.plugin import PluginContext


def int0_low(ctx: PluginContext) -> None:
    ctx.drive_pin(3, 2, 0)
    print("cycle", ctx.cycle, "INT0 low")


def int0_high(ctx: PluginContext) -> None:
    ctx.drive_pin(3, 2, 1)
    print("cycle", ctx.cycle, "INT0 high")


def write_ram(ctx: PluginContext) -> None:
    ctx.write("iram", 0x20, 0x5A)
    print("cycle", ctx.cycle, "PC", hex(ctx.pc), "RAM[0x20] = 0x5A")


def setup(ctx: PluginContext) -> None:
    ctx.schedule_at(20, int0_low)
    ctx.schedule_at(30, int0_high)
    ctx.schedule_at(100, write_ram)
