#!/usr/bin/env python3
"""Tests for bounded tmux/Codex TUI transcript lifecycle management."""

from __future__ import annotations

import contextlib
import importlib.util
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
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


manager = load_script_module("codex_tmux_manager_tui_log_under_test", SCRIPT_DIR / "codex_tmux_manager.py")


def oversized_log(path: Path, marker: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((marker + b"\n") * 400)


class TuiLogClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-tui-log-")
        self.base = Path(self.temp.name) / "state"
        self.logs = self.base / "logs"
        self.logs.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def registry(self) -> dict:
        workers = {}
        for name in ("active", "terminal", "inactive"):
            workers[name] = {
                "name": name,
                "session": f"audit-{name}",
                "window": "codex",
                "session_mode": "independent",
                "mode": "interactive",
                "log_file": str(self.logs / f"{name}.log"),
            }
        workers["terminal"]["stopped_at"] = "2026-06-04T00:00:00+08:00"
        return {"version": 1, "session": "audit", "workers": workers}

    def test_default_compacts_closed_terminal_and_orphan_only(self) -> None:
        for name in ("active", "terminal", "inactive", "orphan", "orphan-open"):
            oversized_log(self.logs / f"{name}.log", name.encode("ascii"))
        before = {path.name: path.stat().st_size for path in self.logs.glob("*.log")}
        orphan_open_inode = manager.path_inode(self.logs / "orphan-open.log")

        def target_present(target: str) -> bool:
            return target == "audit-active:codex"

        with (
            mock.patch.object(manager, "tmux_target_present", side_effect=target_present),
            mock.patch.object(manager, "open_file_inodes", return_value={orphan_open_inode}),
        ):
            dry = manager.compact_tui_logs(
                self.base,
                self.registry(),
                active_max_bytes=100,
                closed_max_bytes=100,
                keep_bytes=40,
                dry_run=True,
            )

        actions = {Path(item["path"]).name: item["action"] for item in dry}
        self.assertEqual(actions["active.log"], "skipped-active")
        self.assertEqual(actions["terminal.log"], "would-compact")
        self.assertEqual(actions["inactive.log"], "skipped-inactive")
        self.assertEqual(actions["orphan.log"], "would-compact")
        self.assertEqual(actions["orphan-open.log"], "skipped-open")
        self.assertEqual(before, {path.name: path.stat().st_size for path in self.logs.glob("*.log")})

        with (
            mock.patch.object(manager, "tmux_target_present", side_effect=target_present),
            mock.patch.object(manager, "open_file_inodes", return_value={orphan_open_inode}),
        ):
            applied = manager.compact_tui_logs(
                self.base,
                self.registry(),
                active_max_bytes=100,
                closed_max_bytes=100,
                keep_bytes=40,
                dry_run=False,
            )

        applied_actions = {Path(item["path"]).name: item["action"] for item in applied}
        self.assertEqual(applied_actions["terminal.log"], "compacted")
        self.assertEqual(applied_actions["orphan.log"], "compacted")
        self.assertLessEqual((self.logs / "terminal.log").stat().st_size, 40)
        self.assertLessEqual((self.logs / "orphan.log").stat().st_size, 40)
        self.assertEqual((self.logs / "active.log").stat().st_size, before["active.log"])
        self.assertEqual((self.logs / "inactive.log").stat().st_size, before["inactive.log"])
        self.assertEqual((self.logs / "orphan-open.log").stat().st_size, before["orphan-open.log"])

    def test_active_rotation_rebinds_pipe_without_dash_o(self) -> None:
        path = self.logs / "active.log"
        oversized_log(path, b"active")
        registry = self.registry()
        before = path.stat().st_size
        with (
            mock.patch.object(manager, "tmux_target_present", return_value=True),
            mock.patch.object(manager, "open_file_inodes", return_value=set()),
            mock.patch.object(manager, "tmux") as tmux_mock,
        ):
            outcomes = manager.compact_tui_logs(
                self.base,
                registry,
                active_max_bytes=100,
                closed_max_bytes=100,
                keep_bytes=40,
                include_active=True,
                include_orphans=False,
                dry_run=False,
            )

        active = next(item for item in outcomes if item["name"] == "active")
        self.assertEqual(active["action"], "rotated-active")
        self.assertLess(path.stat().st_size, before)
        previous = manager.active_tui_log_previous_path(path)
        self.assertTrue(previous.is_file())
        self.assertLessEqual(previous.stat().st_size, 40)
        call = tmux_mock.call_args_list[0]
        self.assertEqual(call.args[:3], ("pipe-pane", "-t", "audit-active:codex"))
        self.assertNotIn("-o", call.args)


@unittest.skipUnless(shutil.which("tmux"), "tmux is required for isolated pipe-pane rotation simulation")
class IsolatedTmuxTuiLogRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-tui-log-tmux-")
        self.root = Path(self.temp.name)
        self.path = self.root / "logs" / "active.log"
        self.path.parent.mkdir(parents=True)
        self.socket = f"codex-tui-log-{uuid.uuid4().hex[:8]}"
        self.target = "audit-active:codex.0"

    def tearDown(self) -> None:
        self.tmux("kill-server", check=False)
        self.temp.cleanup()

    def tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["tmux", "-L", self.socket, *args], text=True, capture_output=True, check=check)

    def wait_for_size(self, path: Path, minimum: int, timeout: float = 3.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if path.is_file() and path.stat().st_size >= minimum:
                return
            time.sleep(0.05)
        self.fail(f"{path} did not reach {minimum} bytes")

    def test_active_pipe_continues_writing_after_rotation(self) -> None:
        self.tmux(
            "new-session",
            "-d",
            "-s",
            "audit-active",
            "-n",
            "codex",
            "bash",
            "-lc",
            "while true; do date +%s%N; sleep 0.02; done",
        )
        self.tmux("pipe-pane", "-t", self.target, f"cat >> {self.path}")
        self.wait_for_size(self.path, 100)
        before = self.path.stat().st_size

        with mock.patch.object(manager, "tmux", self.tmux):
            result = manager.rotate_active_tui_log(
                self.path,
                self.target,
                max_bytes=1,
                keep_bytes=80,
                dry_run=False,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "rotated-active")
        previous = manager.active_tui_log_previous_path(self.path)
        self.assertTrue(previous.is_file())
        self.assertLessEqual(previous.stat().st_size, 80)
        self.wait_for_size(self.path, 40)
        self.assertLess(self.path.stat().st_size, before)
        self.assertEqual(self.tmux("display-message", "-p", "-t", self.target, "#{pane_pipe}").stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
