#!/usr/bin/env python3
"""Tests for bounded live coordinator-constraint lifecycle."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent


def load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manager = load_script_module("codex_tmux_manager_constraints_under_test", SCRIPT_DIR / "codex_tmux_manager.py")


class ConstraintLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-constraints-")
        self.base = Path(self.temp.name) / "state"
        self.path = manager.coordinator_constraints_path(self.base)
        manager.write_text(self.path, "old-live-constraints\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_replace_archives_exact_previous_live_version_once(self) -> None:
        archive = manager.replace_constraints_doc(self.base, "new-live-constraints\n")

        self.assertIsNotNone(archive)
        self.assertEqual(archive.parent, manager.coordinator_constraints_archive_dir(self.base))
        self.assertEqual(archive.read_bytes(), b"old-live-constraints\n")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "new-live-constraints\n")

        second = manager.replace_constraints_doc(self.base, "new-live-constraints\n")
        self.assertIsNone(second)
        self.assertEqual(len(list(manager.coordinator_constraints_archive_dir(self.base).glob("*.md"))), 1)

    def test_set_file_archives_previous_live_version_and_records_pointer(self) -> None:
        source = Path(self.temp.name) / "current.md"
        source.write_text("concise-current-constraints\n", encoding="utf-8")
        args = argparse.Namespace(
            state_dir=str(self.base),
            session="audit",
            print=False,
            append=None,
            set_file=str(source),
            reset_defaults=False,
            tensorboard_port_range=None,
        )

        with (
            mock.patch.object(manager, "refresh_schedule_doc"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            manager.cmd_constraints(args)

        archives = list(manager.coordinator_constraints_archive_dir(self.base).glob("*.md"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), b"old-live-constraints\n")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "concise-current-constraints\n")
        event = json.loads(manager.coordinator_constraints_events_path(self.base).read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["event"], "set-file")
        self.assertEqual(event["data"]["archive"], str(archives[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
