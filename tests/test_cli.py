import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sub1.hex"


class CLITests(unittest.TestCase):
    def test_help_printed_on_start(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="help\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("Commands"), 2)  # startup + help command
        for text in ("Command", "Aliases", "Arguments", "break cycle", "SPACE ADDR [N]", "Numbers", "Display"):
            self.assertIn(text, proc.stdout)

    def test_help_columns_align(self):
        from simu8051.cli import HELP_NOTES, HELP_TEXT

        lines = HELP_TEXT.splitlines()
        header = next(line for line in lines if line.startswith("  Command"))
        alias_col = header.index("Aliases")
        argument_col = header.index("Arguments")
        description_col = header.index("Description")
        note_prefixes = tuple(f"  {label}" for label, _ in HELP_NOTES)
        for line in lines:
            self.assertEqual(line, line.rstrip(), line)
            if line.startswith("  ---") or line.startswith(note_prefixes):
                continue
            if len(line) <= description_col:
                continue
            self.assertEqual(line[alias_col - 1], " ", line)
            self.assertEqual(line[argument_col - 1], " ", line)
            self.assertEqual(line[description_col - 1], " ", line)

    def test_command_aliases(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="b 0x0030\nr\ns 2\nq\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("breakpoint at 0x0030", proc.stdout)
        self.assertIn("pc breakpoint 0x0030", proc.stdout)
        self.assertIn("0x0036", proc.stdout)

    def test_subcommand_aliases(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="display sfr TCON\ni dp\nb cy 10\nq\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1: [on] sfr TCON", proc.stdout)
        self.assertIn("breakpoint at cycle 10", proc.stdout)

    def test_regs_command(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="reg\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("│ PC", proc.stdout)
        self.assertIn("│ T0", proc.stdout)

    def test_batch_run(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE), "--batch", "--max-cycles", "20"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("T0", proc.stdout)

    def test_interactive_display_and_break(self):
        script = "step\ndisplay sfr TCON\nstep\nundisplay 1\nbreak 0x0033\ncontinue\nquit\n"
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            input=script,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("display 1: sfr TCON", proc.stdout)
        self.assertIn("[1] sfr TCON:", proc.stdout)
        self.assertIn("breakpoint at 0x0033", proc.stdout)

    def test_invalid_break_option_exits_cleanly(self):
        for spec in ("foo", "cycle:foo"):
            proc = subprocess.run(
                [sys.executable, "-m", "simu8051", str(FIXTURE), "--batch", "--break", spec],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(proc.returncode, 2, spec)
            self.assertIn("error:", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_missing_command_arguments(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="break cycle\nundisplay\nenable\ndisable\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.count("error:"), 4)
        self.assertNotIn("Traceback", proc.stderr)

    def test_bad_hex_exit_code(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", "does-not-exist.hex", "--batch"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error:", proc.stderr)

    def test_display_only_runs_after_trigger_commands(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input=(
                "display sfr TCON\n"
                "reg\n"
                "x i 0x20 1\n"
                "help\n"
                "d 1\n"
                "info display\n"
                "w i 0x20 1\n"
                "disable 1\n"
                "step\n"
                "enable 1\n"
                "step\n"
                "\n"
                "quit\n"
            ),
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("[1] sfr TCON:"), 3)

    def test_display_runs_after_load(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="display x i 0x20 1\nload tests/fixtures/sub2.hex\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("[1] x i 0x20 1:"), 2)

    def test_display_memory(self):
        proc = subprocess.run(
            [
                sys.executable, "-m", "simu8051", str(FIXTURE),
                "--batch", "--max-cycles", "2",
                "--display", "x i 0x20 4",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.count("[1] x i 0x20 4:"), 1)

    def test_disassemble_count_starts_at_pc(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="d 2\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0000  020030   LJMP    0x0030", proc.stdout)
        self.assertIn("0003  ff", proc.stdout)
        self.assertEqual(proc.stdout.count("NULL"), 1)

    def test_unwritten_code_shows_null(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="x c 3 2\nd 2 3\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0x0000: 02 00 30 FF FF FF FF FF", proc.stdout)
        self.assertIn("0003  ff", proc.stdout)
        self.assertIn("NULL", proc.stdout)

    def test_current_step_after_execution(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="step\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PC=0x0030  MOV", proc.stdout)

    def test_memory_and_control_commands(self):
        script = (
            "x i 0 0x10\n"
            "x i 0x20 2\n"
            "x x 0 2\n"
            "x c 0 2\n"
            "w i 0x20 0x5A\n"
            "w x 0 0x12 0x34\n"
            "w c 0x0030 0x00\n"
            "x i 0x20 1\n"
            "x x 0 2\n"
            "x c 0x0030 1\n"
            "disassemble 1 0x0030\n"
            "reset cold\n"
            "load tests/fixtures/sub2.hex\n"
            "step\n"
            "quit\n"
        )
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            input=script,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for expected in (
            "0x00: 00 00 00 00 00 00 00 00",
            "0x08: 00 00 00 00 00 00 00 00",
            "0x20: 00 00",
            "0x0000: 00 00",
            "0x0000: 02 00",
            "0x20: 5A",
            "0x0000: 12 34",
            "0x0030: 00",
            "0030  00", "reset", "loaded",
        ):
            self.assertIn(expected, proc.stdout)

    def test_memory_format_always_full_rows(self):
        from simu8051.cli_format import format_mem
        from simu8051.session import Session

        session = Session.from_bytes(b"\x00")
        for count in (None, 0, 1, 9):
            text = format_mem(session, "i", 0x31, count)
            for line in text.splitlines():
                self.assertEqual(len(line.split(": ", 1)[1].split()), 8)
            self.assertTrue(text.startswith("0x30:"))
            self.assertNotIn("0x31:", text)

    def test_memory_dump_aligns_full_rows(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="x i 0x31\nx i 0x31 0x11\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0x30: 00 00 00 00 00 00 00 00", proc.stdout)
        self.assertIn("0x38: 00 00 00 00 00 00 00 00", proc.stdout)
        self.assertIn("0x40: 00 00 00 00 00 00 00 00", proc.stdout)
        self.assertNotIn("0x31:", proc.stdout)

    def test_display_rejects_regs(self):
        from simu8051.cli import parse_display

        for name in ("regs", "reg"):
            with self.assertRaises(ValueError):
                parse_display([name])

    def test_display_rejects_bad_arguments(self):
        from simu8051.cli import parse_display

        for args in (
            ["x"],
            ["x", "0x200", "0x100"],
            ["x", "q", "0"],
            ["x", "i", "0", "1", "2"],
            ["disassemble", "x"],
            ["disassemble", "1", "2", "3"],
            ["sfr", "A", "B"],
        ):
            with self.assertRaises(ValueError, msg=args):
                parse_display(args)

    def test_invalid_display_is_not_registered(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="display x 0x200 0x100\ninfo display\nquit\n",
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: display x i|x|c ADDR [N]", proc.stderr)
        self.assertNotIn("1: [on]", proc.stdout)
        self.assertNotIn("<display error:", proc.stdout)

    def test_invalid_display_option_exits_cleanly(self):
        proc = subprocess.run(
            [
                sys.executable, "-m", "simu8051", str(FIXTURE),
                "--batch", "--display", "x 0x200 0x100",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage: display x i|x|c ADDR [N]", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_register_changes_are_red(self):
        from unittest import mock

        from simu8051.cli import CLI
        from simu8051.cli_format import RED, RESET
        from simu8051.session import Session

        session = Session.from_bytes(b"\x00")
        cli = CLI(session, [])
        with mock.patch("simu8051.cli_format.use_color", return_value=True):
            first = cli.registers()
            session.set_reg("A", 0x42)
            session.step()
            second = cli.registers()
        self.assertNotIn("\033", first)
        self.assertIn(f"{RED}0x42{RESET}", second)
        self.assertIn(f"{RED}0x0001{RESET}", second)  # PC changed
        self.assertIn(f"{RED}1{RESET}", second)  # cycle changed

    def test_display_marks_changed_bytes(self):
        from unittest import mock

        from simu8051.cli import CLI, DisplayItem
        from simu8051.cli_format import RED, RESET
        from simu8051.session import Session

        session = Session.from_bytes(b"\x00")
        item = DisplayItem(["x", "i", "0x20", "1"], "x i 0x20 1")
        cli = CLI(session, [item])
        with mock.patch("simu8051.cli_format.use_color", return_value=True):
            first = cli.display_text(item)
            session.write("iram", 0x20, 0x5A)
            second = cli.display_text(item)
            cli.reset_baselines()
            third = cli.display_text(item)
        self.assertNotIn("\033", first)
        self.assertEqual(second.count(RED), 1)
        self.assertIn(f"{RED}5A{RESET}", second)
        self.assertNotIn("\033", third)

    def test_colour_disabled_without_tty_or_no_color(self):
        from unittest import mock

        from simu8051 import cli_format

        with mock.patch("sys.stdout.isatty", return_value=False):
            self.assertFalse(cli_format.use_color())
        with mock.patch("sys.stdout.isatty", return_value=True):
            with mock.patch.dict("os.environ", {"NO_COLOR": ""}):
                self.assertFalse(cli_format.use_color())
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertTrue(cli_format.use_color())

    def test_memory_write_errors(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            input="w i 0x80 1\nw i 0x20 0x100\nx q 0\nquit\n",
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr.count("error:"), 3)


if __name__ == "__main__":
    unittest.main()
