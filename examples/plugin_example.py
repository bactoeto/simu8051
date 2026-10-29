"""最简单的 8051 激励插件示例。

用法：
    python3 -m simu8051 firmware.hex --plugin examples/plugin_example.py

行为：
1. 第 20 个机器周期把 INT0 / P3.2 拉低；
2. 第 30 个机器周期释放 INT0；
3. 第 100 个机器周期把内部 RAM 0x20 写成 0x5A，并打印当前 PC。
"""
from simu8051.session import PluginContext


def on_cycle(ctx: PluginContext) -> None:
    if ctx.cycle == 20:
        ctx.drive_pin(3, 2, 0)
        print("cycle", ctx.cycle, "INT0 low")
    elif ctx.cycle == 30:
        ctx.drive_pin(3, 2, 1)
        print("cycle", ctx.cycle, "INT0 high")
    elif ctx.cycle == 100:
        ctx.write("iram", 0x20, 0x5A)
        print("cycle", ctx.cycle, "PC", hex(ctx.pc), "RAM[0x20] = 0x5A")
