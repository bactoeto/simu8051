import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DataTests(unittest.TestCase):
    def test_check_data(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_data.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('"ok": true', proc.stdout)

    def test_counts(self):
        isa = json.loads((ROOT / "data" / "isa.json").read_text())
        mem = json.loads((ROOT / "data" / "memory.json").read_text())
        self.assertEqual(isa["counts"]["instructions"], 111)
        self.assertEqual(isa["counts"]["valid_opcodes"], 255)
        self.assertEqual(isa["counts"]["opcodes"], 256)
        self.assertEqual(len(mem["sfrs"]), 21)


if __name__ == "__main__":
    unittest.main()
