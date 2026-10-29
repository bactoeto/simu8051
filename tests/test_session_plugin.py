import tempfile
import unittest
from pathlib import Path

from simu8051.session import Plugin, PluginContext, Session


def session_with(code):
    return Session.from_bytes(bytes(code))


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

    def on_cycle(self, ctx: PluginContext) -> None:
        self.events.append("cycle")

    def on_instruction(self, ctx: PluginContext, text: str) -> None:
        self.instructions.append(text)

    def on_stop(self, ctx: PluginContext, reason: str) -> None:
        self.events.append(("stop", reason))


class SessionPluginTests(unittest.TestCase):
    def test_step_instruction_and_hooks(self):
        s = session_with([0x00, 0x04])
        p = CountingPlugin()
        s.add_plugin(p)
        s.step()
        self.assertEqual(s.pc, 1)
        self.assertEqual(p.cycles, 1)
        self.assertEqual(p.instructions, 1)

    def test_on_cycle_write(self):
        s = session_with([0x00, 0x00, 0x00])
        seen = {}

        class AtCycle(Plugin):
            def on_cycle(self, ctx: PluginContext) -> None:
                if ctx.cycle == 2:
                    ctx.write("iram", 0x20, 0x5A)
                    seen["cycle"] = ctx.cycle

        s.add_plugin(AtCycle())
        s.step_cycle()
        s.step_cycle()
        self.assertEqual(s.read("direct", 0x20), 0x5A)
        self.assertEqual(seen["cycle"], 2)

    def test_pc_breakpoint(self):
        s = session_with([0x00, 0x00, 0x00])
        s.break_at(1)
        s.step()
        r = s.step()
        assert r.reason is not None
        self.assertEqual(r.reason.kind, "pc_breakpoint")
        self.assertEqual(s.pc, 1)

    def test_lifecycle_hooks(self):
        s = session_with([0x00, 0x00])
        p = LifecyclePlugin()
        s.add_plugin(p)
        self.assertEqual(p.events.count("setup"), 1)
        s.step()
        self.assertIn("cycle", p.events)
        self.assertEqual(p.instructions, ["NOP"])
        s.run(max_instructions=1)
        self.assertTrue(any(isinstance(e, tuple) and e[0] == "stop" for e in p.events))

    def test_run_breakpoint_then_step(self):
        s = session_with([0x02, 0x00, 0x03, 0x00])
        s.break_at(3)
        first = s.run()
        self.assertEqual(s.pc, 3)
        assert first.reason is not None
        self.assertEqual(first.reason.kind, "pc_breakpoint")
        second = s.step()
        self.assertEqual(second.cycles, 1)
        self.assertEqual(s.pc, 4)

    def test_step_after_breakpoint(self):
        s = session_with([0x00, 0x00])
        s.break_at(0)
        first = s.step()
        assert first.reason is not None
        self.assertEqual(first.reason.kind, "pc_breakpoint")
        second = s.step()
        self.assertEqual(second.cycles, 1)
        self.assertEqual(s.pc, 1)

    def test_load_function_plugin(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plug.py"
            path.write_text(
                "def on_cycle(ctx):\n"
                "    if ctx.cycle == 1:\n"
                "        ctx.write('iram', 0x21, 0x77)\n"
            )
            s = session_with([0x00, 0x00])
            s.load_plugin(path)
            s.step()
            self.assertEqual(s.read("direct", 0x21), 0x77)

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
            p = s.load_plugin(path)
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
                session_with([0x00]).load_plugin(path)

    def test_class_plugin_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "plug.py"
            path.write_text("class Stimulus:\n    pass\n")
            with self.assertRaises(ImportError):
                session_with([0x00]).load_plugin(path)


if __name__ == "__main__":
    unittest.main()
