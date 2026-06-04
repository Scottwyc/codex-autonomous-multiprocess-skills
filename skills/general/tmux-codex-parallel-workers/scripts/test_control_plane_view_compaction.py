#!/usr/bin/env python3
"""Tests for concise, non-duplicated live control-plane views."""

from __future__ import annotations

import importlib.util
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


manager = load_script_module("codex_tmux_manager_compaction_under_test", SCRIPT_DIR / "codex_tmux_manager.py")


class ControlPlaneViewCompactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-control-view-")
        self.base = Path(self.temp.name) / "state"
        manager.write_text(manager.coordinator_constraints_path(self.base), "# Current constraints\n\n- concise\n")
        workers = {}
        for index in range(20):
            name = f"worker-{index:02d}"
            progress = self.base / "progress" / f"{name}.md"
            report = self.base / "reports" / f"{name}.md"
            workplan = self.base / "workplans" / f"{name}.md"
            manager.write_text(progress, f"# Progress\n\ncurrent summary {index}\n")
            manager.write_text(report, "# Report\n\nPending\n")
            manager.write_text(workplan, f"# Work Plan\n\n## Task\n\nbounded task {index}\n")
            workers[name] = {
                "name": name,
                "session": f"cw-{name}",
                "window": "codex",
                "worker_kind": "autonomous-experiment",
                "parent_worker": "main",
                "resources": [f"slot:{index}"],
                "owned_paths": [str(self.base / "outputs" / name)],
                "progress_file": str(progress),
                "report_file": str(report),
                "workplan_file": str(workplan),
                "jobs_file": str(self.base / "jobs" / f"{name}.json"),
                "status_file": str(self.base / "status" / f"{name}.json"),
                "log_file": str(self.base / "logs" / f"{name}.log"),
            }
        self.registry = {
            "version": 1,
            "session": "cw",
            "session_namespace": "cw",
            "session_mode": "independent",
            "mission": "Keep the live control plane concise.",
            "workers": workers,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_live_views_render_each_worker_once_without_detail_blocks(self) -> None:
        with mock.patch.object(manager, "tmux_target_present", return_value=True):
            schedule = manager.render_schedule_doc(self.base, self.registry)
            consult = manager.render_consult_context(self.base, self.registry)
            handoff = manager.render_coordinator_handoff(self.base, self.registry)
            memory = manager.render_coordinator_memory(self.base, self.registry)

        for text in (schedule, consult, handoff, memory):
            self.assertIn("worker-00", text)
            self.assertIn("worker-19", text)
            self.assertNotIn("### worker-", text)

        self.assertNotIn("## 当前 Worker 明细", schedule)
        self.assertNotIn("## 当前 Worker 关键文件", consult)
        self.assertNotIn("## Current Worker Key Files", handoff)
        self.assertNotIn("## Resource And Ownership Snapshot", memory)
        self.assertLess(len(schedule.splitlines()), 160)
        self.assertLess(len(consult.splitlines()), 160)
        self.assertLess(len(handoff.splitlines()), 160)
        self.assertLess(len(memory.splitlines()), 160)


if __name__ == "__main__":
    unittest.main(verbosity=2)
