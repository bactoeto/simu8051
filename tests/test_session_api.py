"""Contract tests for the public Session API.

These tests import only simu8051.session. If they need an internal module,
the public interface is missing something.
"""
import ast
import tempfile
import unittest
from pathlib import Path

from simu8051.session import (
    HexError,
    Instruction,
    Plugin,
    PluginContext,
    RunResult,
    Session,
    StepResult,
    StopReason,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sub1.hex"

LJMP_0030 = bytes([0x02, 0x00, 0x30])
NOP = bytes([0x00])
NOP_NOP = bytes([0x00, 0x00])


class ConstructionTests(unittest.TestCase):
    def test_open_fixture(self):
        session = Session.open(FIXTURE)
        self.assertEqual(session.pc, 0)
        self.assertEqual(session.cycle, 0)
        self.assertFalse(session.halted)
        self.assertFalse(session.idle)

    def test_from_bytes_entry(self):
        session = Session.from_bytes(NOP, entry=0x30)
        self.assertEqual(session.pc, 0x30)

    def test_open_honours_hex_start_record(self):
        text = (
            ":0100000000FF\n"
            ":0400000500000030C7\n"
            ":00000001FF\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "start.hex"
            path.write_text(text, encoding="ascii")
            session = Session.open(path)
            self.assertEqual(session.pc, 0x30)

    def test_reset_returns_to_entry(self):
        session = Session.from_bytes(NOP, entry=0x30)
        session.set_reg("PC", 0)
        session.reset()
        self.assertEqual(session.pc, 0x30)

    def test_cold_and_warm_reset(self):
        session = Session.from_bytes(NOP)
        session.write("iram", 0x20, 0x5A)
        session.write("xdata", 0x10, 0xA5)

        session.reset(cold=False)
        self.assertEqual(session.read("iram", 0x20), 0x5A)
        self.assertEqual(session.read("xdata", 0x10), 0xA5)

        session.reset(cold=True)
        self.assertEqual(session.read("iram", 0x20), 0x00)
        self.assertEqual(session.read("xdata", 0x10), 0x00)

    def test_malformed_hex_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.hex"
            path.write_text("not a record\n", encoding="ascii")
            with self.assertRaises(HexError):
                Session.open(path)

    def test_failed_load_leaves_session_intact(self):
        session = Session.from_bytes(NOP, entry=0x10)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.hex"
            path.write_text("not a record\n", encoding="ascii")
            with self.assertRaises(HexError):
                session.load(path)
        self.assertEqual(session.pc, 0x10)
        self.assertEqual(session.decode(0).syntax, "NOP")


class ExecutionTests(unittest.TestCase):
    def test_step_result(self):
        session = Session.from_bytes(LJMP_0030)
        result = session.step()
        self.assertIsInstance(result, StepResult)
        self.assertEqual(result.pc, 0)
        self.assertEqual(result.next_pc, 0x30)
        self.assertEqual(result.cycles, 2)
        self.assertTrue(result.completed)
        self.assertIsNone(result.reason)
        self.assertIsInstance(result.instruction, Instruction)
        assert result.instruction is not None
        self.assertEqual(result.instruction.syntax, "LJMP addr16")

    def test_step_cycle(self):
        session = Session.from_bytes(LJMP_0030)
        first = session.step_cycle()
        self.assertFalse(first.completed)
        self.assertEqual(session.pc, 0)
        second = session.step_cycle()
        self.assertTrue(second.completed)
        self.assertEqual(session.pc, 0x30)

    def test_run_instruction_limit(self):
        session = Session.from_bytes(LJMP_0030)
        result = session.run(max_instructions=1)
        self.assertIsInstance(result, RunResult)
        self.assertEqual(result.reason.kind, "max_instructions")
        self.assertEqual(result.instructions, 1)
        self.assertEqual(result.cycles, 2)
        self.assertEqual(result.start_pc, 0)
        self.assertEqual(result.end_pc, 0x30)

    def test_run_cycle_limit_stops_mid_instruction(self):
        session = Session.from_bytes(LJMP_0030)
        first = session.run(max_cycles=1)
        self.assertEqual(first.reason.kind, "max_cycles")
        self.assertEqual(first.instructions, 0)
        self.assertEqual(first.cycles, 1)
        self.assertEqual(session.pc, 0)

        second = session.step()
        self.assertTrue(second.completed)
        self.assertEqual(second.cycles, 1)
        self.assertEqual(session.pc, 0x30)

    def test_trace_emits_once_per_instruction(self):
        lines: list[str] = []
        session = Session.from_bytes(LJMP_0030)
        session.set_trace(lines.append)
        session.run(max_cycles=1)
        session.step()
        self.assertEqual(len(lines), 1)
        self.assertIn("LJMP", lines[0])

    def test_zero_limits(self):
        session = Session.from_bytes(NOP)
        result = session.run(max_cycles=0)
        self.assertEqual(result.reason.kind, "max_cycles")
        self.assertEqual(result.instructions, 0)
        self.assertEqual(result.cycles, 0)

        session = Session.from_bytes(NOP)
        result = session.run(max_instructions=0)
        self.assertEqual(result.reason.kind, "max_instructions")
        self.assertEqual(result.instructions, 0)

    def test_cycle_breakpoint(self):
        session = Session.from_bytes(LJMP_0030)
        session.break_at_cycle(1)
        result = session.run()
        self.assertEqual(result.reason.kind, "cycle_breakpoint")
        self.assertEqual(result.reason.cycle, 1)
        self.assertEqual(result.instructions, 0)

    def test_pc_breakpoint_and_continue(self):
        session = Session.from_bytes(NOP_NOP)
        session.break_at(1)

        first = session.step()
        self.assertIsNone(first.reason)
        self.assertEqual(session.pc, 1)

        stopped = session.step()
        assert stopped.reason is not None
        self.assertEqual(stopped.reason.kind, "pc_breakpoint")
        self.assertEqual(stopped.reason.pc, 1)
        self.assertFalse(stopped.completed)
        assert session.last_stop is not None
        self.assertEqual(session.last_stop.kind, "pc_breakpoint")

        resumed = session.step()
        self.assertTrue(resumed.completed)
        self.assertEqual(session.pc, 2)
        self.assertIsNone(session.last_stop)

    def test_halt_is_reported(self):
        session = Session.from_bytes(NOP)
        session.set_sfr("PCON", 0x02)
        result = session.step()
        self.assertTrue(result.halted)
        halted = session.step()
        assert halted.reason is not None
        self.assertEqual(halted.reason.kind, "halt")

    def test_trap_becomes_stop_reason(self):
        session = Session.from_bytes(bytes([0xA5]))
        result = session.step()
        assert result.reason is not None
        self.assertEqual(result.reason.kind, "trap")
        self.assertIn("reserved opcode 0xA5", result.reason.detail)
        assert session.last_stop is not None
        self.assertEqual(session.last_stop.kind, "trap")


class InspectionTests(unittest.TestCase):
    def test_memory_spaces(self):
        session = Session.from_bytes(NOP)
        session.write("iram", 0x20, 0x5A)
        session.write("xdata", 0x10, 0xA5)
        session.write("bit", 0x08, 1)
        session.write("sfr", 0x90, 0x0F)
        session.write("direct", 0x30, 0x11)

        self.assertEqual(session.read("iram", 0x20), 0x5A)
        self.assertEqual(session.read("xdata", 0x10), 0xA5)
        self.assertEqual(session.read("bit", 0x08), 1)
        self.assertEqual(session.read("sfr", 0x90), 0x0F)
        self.assertEqual(session.read("direct", 0x30), 0x11)

    def test_unwritten_byte_is_not_loaded_but_reads_ff(self):
        session = Session.from_bytes(bytes([0xFF, 0x00]))
        self.assertTrue(session.is_loaded(0))
        self.assertTrue(session.is_loaded(1))
        self.assertFalse(session.is_loaded(2))
        self.assertEqual(session.read("code", 0), 0xFF)
        self.assertEqual(session.read("code", 2), 0xFF)
        self.assertEqual(session.format(session.decode(2)), "NULL")

    def test_hex_coverage_marks_only_file_data(self):
        session = Session.open(FIXTURE)
        self.assertTrue(session.is_loaded(0))
        self.assertTrue(session.is_loaded(0x30))
        self.assertFalse(session.is_loaded(3))
        self.assertEqual(session.format(session.decode(3)), "NULL")

    def test_code_write_marks_coverage(self):
        session = Session.from_bytes(bytes([0x00]))
        self.assertFalse(session.is_loaded(1))
        session.write("code", 1, 0xFF)
        self.assertTrue(session.is_loaded(1))
        self.assertEqual(session.format(session.decode(1)), "MOV     R7,A")

    def test_partial_instruction_is_null(self):
        session = Session.from_bytes(bytes([0x02]))
        self.assertEqual(session.format(session.decode(0)), "NULL")
        session.write("code", 1, 0x00)
        session.write("code", 2, 0x30)
        self.assertIn("LJMP", session.format(session.decode(0)))

    def test_unknown_space(self):
        session = Session.from_bytes(NOP)
        with self.assertRaises(ValueError):
            session.read("bogus", 0)
        with self.assertRaises(ValueError):
            session.write("bogus", 0, 0)

    def test_code_write_changes_disassembly(self):
        session = Session.from_bytes(LJMP_0030)
        self.assertEqual(session.decode(0).syntax, "LJMP addr16")
        session.write("code", 0, 0x00)
        self.assertEqual(session.decode(0).syntax, "NOP")

    def test_sfr_latch_versus_pin_level(self):
        session = Session.from_bytes(NOP)
        session.set_sfr("P1", 0x55)
        session.drive_pin(1, 0, 0)
        session.step_cycle()
        self.assertEqual(session.read("sfr", 0x90), 0x55)
        self.assertEqual(session.read("direct", 0x90), 0x54)

    def test_registers_and_names(self):
        session = Session.from_bytes(NOP)
        session.set_reg("A", 0x7F)
        session.set_reg("DPTR", 0x1234)
        self.assertEqual(session.get_reg("A"), 0x7F)
        self.assertEqual(session.get_reg("DPTR"), 0x1234)
        self.assertIn("A", session.register_names)
        self.assertIn("PC", session.register_names)

        session.set_sfr("P1", 0x5A)
        self.assertEqual(session.get_sfr("P1"), 0x5A)
        self.assertEqual(session.get_sfr(0x90), 0x5A)
        self.assertIn("P1", session.sfr_names)
        self.assertIn("ACC", session.sfr_names)

        with self.assertRaises(KeyError):
            session.get_reg("NOPE")
        with self.assertRaises(KeyError):
            session.get_sfr("NOPE")

    def test_disassembly(self):
        session = Session.from_bytes(LJMP_0030 + NOP)
        first = session.decode()
        self.assertEqual(first.address, 0)
        self.assertIn("LJMP", session.format(first))

        instructions = session.disassemble(0, 2)
        self.assertEqual([item.address for item in instructions], [0, 3])
        self.assertEqual(session.disassemble(0, 0), [])


class PluginTests(unittest.TestCase):
    def test_hooks_and_context(self):
        seen = []

        class Stimulus(Plugin):
            def setup(self, ctx: PluginContext) -> None:
                seen.append("setup")

            def on_cycle(self, ctx: PluginContext) -> None:
                seen.append(("cycle", ctx.cycle))

            def on_instruction(self, ctx: PluginContext, text: str) -> None:
                seen.append(("instruction", text))

            def on_stop(self, ctx: PluginContext, reason: str) -> None:
                seen.append(("stop", reason))

        session = Session.from_bytes(NOP)
        session.add_plugin(Stimulus())
        result = session.run(max_instructions=1)
        self.assertEqual(result.reason.kind, "max_instructions")
        self.assertEqual(seen[0], "setup")
        self.assertIn(("cycle", 1), seen)
        self.assertIn(("instruction", "NOP"), seen)
        self.assertTrue(any(isinstance(item, tuple) and item[0] == "stop" for item in seen))

    def test_context_write_and_stop(self):
        class StopAtCycle(Plugin):
            def on_cycle(self, ctx: PluginContext) -> None:
                if ctx.cycle == 1:
                    ctx.write("iram", 0x20, 0x5A)
                    ctx.stop("done")

        session = Session.from_bytes(NOP)
        session.add_plugin(StopAtCycle())
        result = session.run()
        self.assertEqual(result.reason.kind, "plugin")
        self.assertEqual(result.reason.detail, "done")
        self.assertEqual(session.read("iram", 0x20), 0x5A)

    def test_load_function_plugin(self):
        source = (
            "def on_cycle(ctx):\n"
            "    if ctx.cycle == 1:\n"
            "        ctx.write('iram', 0x21, 0x77)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plug.py"
            path.write_text(source, encoding="ascii")
            session = Session.from_bytes(NOP_NOP)
            session.load_plugin(path)
            session.step()
            self.assertEqual(session.read("iram", 0x21), 0x77)


class BoundaryTests(unittest.TestCase):
    def test_no_internal_attributes(self):
        session = Session.from_bytes(NOP)
        for name in ("core", "image", "plugins"):
            self.assertFalse(hasattr(session, name), name)

    def test_cli_imports_only_session(self):
        tree = ast.parse((ROOT / "simu8051" / "cli.py").read_text(encoding="utf-8"))
        relative = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(relative, {"session"})

    def test_stop_reason_text(self):
        self.assertEqual(str(StopReason("pc_breakpoint", pc=0x30)), "pc breakpoint 0x0030")
        self.assertEqual(str(StopReason("cycle_breakpoint", cycle=10)), "cycle breakpoint 10")
        self.assertEqual(str(StopReason("max_cycles", detail="100")), "max cycles 100")
        self.assertEqual(
            str(StopReason("max_instructions", detail="5")), "max instructions 5"
        )
        self.assertEqual(str(StopReason("halt")), "halted (power-down)")
        self.assertEqual(str(StopReason("plugin", detail="done")), "done")
        self.assertEqual(str(StopReason("plugin")), "plugin stop")


if __name__ == "__main__":
    unittest.main()
