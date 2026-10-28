import os
import unittest
from pathlib import Path
from unittest import mock

from simu8051.cli import completion_candidates, history_path, path_candidates

ROOT = Path(__file__).resolve().parent.parent


class CompletionTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(completion_candidates([], "st"), ["step"])
        self.assertIn("sfr", completion_candidates([], "s"))

    def test_sfr_and_spaces(self):
        self.assertEqual(completion_candidates(["sfr"], "ac"), ["ACC"])
        self.assertEqual(completion_candidates(["x"], "i"), ["i"])
        self.assertEqual(completion_candidates(["w"], "x"), ["x"])

    def test_display(self):
        self.assertEqual(completion_candidates(["display"], "m"), ["mem"])
        self.assertEqual(completion_candidates(["display", "mem"], "x"), ["x"])
        self.assertEqual(completion_candidates(["display", "reg"], "p"), ["PSW", "PC"])

    def test_misc(self):
        self.assertEqual(completion_candidates(["info"], "b"), ["break", "b"])
        self.assertEqual(completion_candidates(["trace"], "o"), ["on", "off"])
        self.assertEqual(completion_candidates(["reset"], "c"), ["cold", "c"])

    def test_load_paths(self):
        prefix = str(ROOT / "tests" / "fixtures" / "sub1")
        self.assertIn(str(ROOT / "tests" / "fixtures" / "sub1.hex"), path_candidates(prefix))


class HistoryPathTests(unittest.TestCase):
    def test_xdg_state_home(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/state"}):
            self.assertEqual(history_path(), Path("/tmp/state/simu8051/history"))

    def test_default(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_STATE_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                history_path(),
                Path.home() / ".local" / "state" / "simu8051" / "history",
            )


if __name__ == "__main__":
    unittest.main()
