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

    def test_command_aliases(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            input="b 0x0030\nr\ns 2\nreg\nq\n",
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
        self.assertIn("2: [on] sfr TCON", proc.stdout)
        self.assertIn("breakpoint at cycle 10", proc.stdout)

    def test_batch_run(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE), "--batch", "--max-cycles", "20"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[1] regs:", proc.stdout)

    def test_interactive_display_and_break(self):
        script = "step\ndisplay sfr TCON\nstep\nundisplay 2\nbreak 0x0033\ncontinue\nquit\n"
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE)],
            cwd=ROOT,
            input=script,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("display 2: sfr TCON", proc.stdout)
        self.assertIn("[2] sfr TCON:", proc.stdout)
        self.assertIn("breakpoint at 0x0033", proc.stdout)

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

    def test_display_memory(self):
        proc = subprocess.run(
            [
                sys.executable, "-m", "simu8051", str(FIXTURE),
                "--batch", "--max-cycles", "2",
                "--display", "mem i 0x20 4",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[2] mem i", proc.stdout)

    def test_memory_and_control_commands(self):
        script = (
            "x i 0x20 2\n"
            "x x 0 2\n"
            "x c 0 2\n"
            "w i 0x20 0x5A\n"
            "w x 0 0x12 0x34\n"
            "w c 0x0030 0x00\n"
            "x i 0x20 1\n"
            "x x 0 2\n"
            "x c 0x0030 1\n"
            "disassemble 0x0030 1\n"
            "reset cold\n"
            "load tests/fixtures/sub2.hex\n"
            "trace on\n"
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
            "i[0x0020..]", "x[0x0000..]", "c[0x0000..]",
            "i[0x0020..]: 5A", "x[0x0000..]: 12 34", "c[0x0030..]: 00",
            "0030  00", "reset", "loaded", "trace on",
        ):
            self.assertIn(expected, proc.stdout)

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

    def test_no_default_display(self):
        proc = subprocess.run(
            [sys.executable, "-m", "simu8051", str(FIXTURE), "--batch", "--max-cycles", "2", "--no-default-display"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("[1] regs:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
