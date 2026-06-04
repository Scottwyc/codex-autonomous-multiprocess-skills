#!/usr/bin/env python3
"""Tests for ordinary supervisor adaptive cadence."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manager = load_script_module("codex_tmux_manager_cadence_under_test", SCRIPT_DIR / "codex_tmux_manager.py")


class SupervisorAdaptiveCadenceTests(unittest.TestCase):
    def next(self, current: int, stable: int, changed: bool, maximum: int = 7200):
        return manager.next_supervisor_cadence(
            base_interval=900,
            current_interval=current,
            max_interval=maximum,
            stable_cycles=stable,
            meaningful_change=changed,
        )

    def test_two_unchanged_cycles_double_interval_and_reset_counter(self) -> None:
        self.assertEqual(self.next(900, 0, False), (900, 1, "hold"))
        self.assertEqual(self.next(900, 1, False), (1800, 0, "widen"))

    def test_meaningful_change_resets_to_base_interval(self) -> None:
        self.assertEqual(self.next(3600, 1, True), (900, 0, "reset"))
        self.assertEqual(self.next(900, 1, True), (900, 0, "hold"))

    def test_widening_is_capped_and_maximum_holds(self) -> None:
        self.assertEqual(self.next(3600, 1, False, maximum=5000), (5000, 0, "widen"))
        self.assertEqual(self.next(5000, 1, False, maximum=5000), (5000, 2, "hold"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
