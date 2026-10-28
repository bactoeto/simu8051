import tempfile
import unittest
from pathlib import Path

from simu8051 import hex as hx
from simu8051.core import Core
from simu8051.plugin import Plugin, PluginContext, load_plugin
from simu8051.step import Session


def session_with(code, name="mem.hex"):
    image = hx.Image()
    image.write(0, bytes(code))
    core = Core(image.code)
    core.reset(cold=True)
    return Session(core=core, image=image)


class CountingPlugin(Plugin):
    def __init__(self):
        self.cycles = 0
        self.instructions = 0

    def on_cycle(self, ctx: PluginContext) -> None:
        self.cycles += 1

    def on_instruction(self, ctx: PluginContext, text: str) -> None:
        self.instructions += 1


class LifecyclePlugin(Plugin):
    def __init__(self):
        self.events = []
        self.instructions = []

    def setup(self, ctx: PluginContext) -> None:
        self.events.append("setup")

    def on_start(self, ctx: PluginContext) -> None:
        self.events.append("start")

    def on_cycle(self, ctx: PluginContext) -> None:
        self.events.append("cycle")

    def on_instruction(self, ctx: PluginContext, text: str) -> None:
        self.instructions.append(text)

    def on_stop(self, ctx: PluginContext, reason: str) -> None:
        self.events.append(("stop", reason))


class StepTests(unittest.TestCase):
    def test_step_instruction_and_hooks(self):
        s = session_with([0x00, 0x04])
        p = CountingPlugin()
        s.plugins.append(p)
        s.step()
        self.assertEqual(s.core.pc, 1)
        self.assertEqual(p.cycles, 1)
        self.assertEqual(p.instructions, 1)

    def test_schedule_cycle_write(self):
        s = session_with([0x00, 0x00, 0x00])
        seen = {}

        def cb(ctx):
            ctx.write("iram", 0x20, 0x5A)
            seen["cycle"] = ctx.cycle

        s.schedule_at(2, cb)
        s.step_cycle()
        s.step_cycle()
        self.assertEqual(s.core.read_direct(0x20), 0x5A)
        self.assertEqual(seen["cycle"], 2)

    def test_pc_breakpoint(self):
        s = session_with([0x00, 0x00, 0x00])
        s.break_at(1)
        s.step()
        r = s.step()
        self.assertIn("breakpoint", r.reason)
        self.assertEqual(s.core.pc, 1)

    def test_lifecycle_hooks(self):
        s = session_with([0x00, 0x00])
        p = LifecyclePlugin()
        p.setup(PluginContext(s, p))  # load_plugin() does this automatically
        s.plugins.append(p)
        s.start()
        s.step()
        self.assertIn("start", p.events)
        self.assertIn("cycle", p.events)
        self.assertEqual(p.instructions, ["NOP"])
        s.run(max_instructions=1)
        self.assertTrue(any(isinstance(e, tuple) and e[0] == "stop" for e in p.events))

    def test_load_function_plugin(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plug.py"
            path.write_text(
                "def setup(ctx):\n"
                "    ctx.schedule_at(1, lambda c: c.write('iram', 0x21, 0x77))\n"
            )
            s = session_with([0x00, 0x00])
            p = load_plugin(path, s)
            s.plugins.append(p)
            s.step()
            self.assertEqual(s.core.read_direct(0x21), 0x77)

    def test_load_hook_functions(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plug.py"
            path.write_text(
                "calls = []\n"
                "def on_cycle(ctx):\n"
                "    calls.append('cycle')\n"
                "def on_instruction(ctx, text):\n"
                "    calls.append('instruction')\n"
                "def on_stop(ctx, reason):\n"
                "    calls.append('stop')\n"
            )
            s = session_with([0x00, 0x00])
            p = load_plugin(path, s)
            s.plugins.append(p)
            s.start()
            s.step()
            s.run(max_instructions=1)
            calls = getattr(p, "_module").calls
            self.assertIn("cycle", calls)
            self.assertIn("instruction", calls)
            self.assertIn("stop", calls)

    def test_plugin_requires_a_hook(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plug.py"
            path.write_text("x = 1\n")
            with self.assertRaises(ImportError):
                load_plugin(path, session_with([0x00]))

    def test_class_plugin_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plug.py"
            path.write_text("class Stimulus:\n    pass\n")
            with self.assertRaises(ImportError):
                load_plugin(path, session_with([0x00]))


if __name__ == "__main__":
    unittest.main()
