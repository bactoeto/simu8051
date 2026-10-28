#!/usr/bin/env python3
"""Run the test suite and verify that every instruction form is executed."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simu8051.core import Core  # noqa: E402


def main() -> int:
    calls: set[str] = set()
    original = Core._execute_current

    def wrapper(self):
        if self._op is not None:
            calls.add(self._op["form"])
        return original(self)

    Core._execute_current = wrapper
    suite = unittest.TestLoader().discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    forms = json.loads((ROOT / "data" / "isa.json").read_text(encoding="utf-8"))["instructions"]
    missing = [form["id"] for form in forms if form["id"] not in calls]
    print(f"executed forms: {len(calls)}/{len(forms)}")
    if missing:
        print("missing:", ", ".join(missing))
    return 0 if result.wasSuccessful() and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
