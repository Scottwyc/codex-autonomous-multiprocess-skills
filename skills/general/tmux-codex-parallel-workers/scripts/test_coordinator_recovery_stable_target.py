#!/usr/bin/env python3
"""Tests for stable-target recovery and related health-supervisor behavior."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
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


manager = load_script_module("codex_tmux_manager_under_test", SCRIPT_DIR / "codex_tmux_manager.py")
health = load_script_module("codex_tmux_health_supervisor_under_test", SCRIPT_DIR / "codex_tmux_health_supervisor.py")


def recovery_args(base: Path, *, old_target: str | None = None, new_target: bool = False, dry_run: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        state_dir=str(base),
        session="audit-ns",
        shared_session=False,
        old_target=old_target,
        cwd=None,
        window=None,
        reason="context-window-exhausted",
        new_target=new_target,
        kill_old=False,
        force=False,
        startup_wait=0,
        dry_run=dry_run,
        model=None,
        reasoning_effort=None,
        no_best_model=False,
        profile=None,
        sandbox=None,
        approval=None,
        search=False,
        inline_tui=False,
    )


def write_registry(base: Path, target: str, cwd: Path) -> None:
    manager.save_registry(
        base,
        {
            "version": 1,
            "session": "audit-ns",
            "session_namespace": "audit-ns",
            "session_mode": "independent",
            "workers": {},
            "coordinator": {
                "role": "main-coordinator",
                "target": target,
                "session": target.split(":", 1)[0],
                "session_namespace": "audit-ns",
                "session_mode": "independent",
                "cwd": str(cwd),
                "restart_window_prefix": "main-recovered",
                "recovery_count": 0,
                "previous_targets": [],
            },
        },
    )


class RecoveryPolicyDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-recovery-policy-")
        self.base = Path(self.temp.name) / "state"
        self.target = "audit-main:stable.0"
        write_registry(self.base, self.target, Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_dry(self, args: argparse.Namespace) -> str:
        output = io.StringIO()
        with (
            mock.patch.object(manager, "require_binary"),
            contextlib.redirect_stdout(output),
        ):
            manager.cmd_recover_coordinator(args)
        return output.getvalue()

    def test_present_registered_target_plans_exact_in_place_respawn(self) -> None:
        with (
            mock.patch.object(manager, "tmux_target_present", return_value=True),
            mock.patch.object(manager, "tmux_target_identity", return_value=("audit-main", "stable", "0")),
        ):
            output = self.run_dry(recovery_args(self.base))

        self.assertIn("recovery_policy=in-place", output)
        self.assertIn(f"would_respawn={self.target}", output)
        self.assertNotIn("main-recovered", output)
        registry = manager.load_registry(self.base)
        self.assertEqual(registry["coordinator"]["target"], self.target)

    def test_missing_target_fails_closed_without_explicit_new_target(self) -> None:
        with mock.patch.object(manager, "tmux_target_present", return_value=False):
            with self.assertRaisesRegex(SystemExit, "fails closed by default"):
                self.run_dry(recovery_args(self.base))

    def test_new_target_is_rejected_while_registered_target_is_present(self) -> None:
        with mock.patch.object(manager, "tmux_target_present", return_value=True):
            with self.assertRaisesRegex(SystemExit, "second main coordinator"):
                self.run_dry(recovery_args(self.base, new_target=True))

    def test_missing_target_new_target_policy_is_explicit(self) -> None:
        with (
            mock.patch.object(manager, "tmux_target_present", return_value=False),
            mock.patch.object(manager, "unique_session_name", return_value="audit-ns-main-recovered-test"),
        ):
            output = self.run_dry(recovery_args(self.base, new_target=True))

        self.assertIn("recovery_policy=new-target", output)
        self.assertIn("would_launch=audit-ns-main-recovered-test:codex", output)

    def test_old_target_override_cannot_drift_from_registry(self) -> None:
        with self.assertRaisesRegex(SystemExit, "refusing recovery target drift"):
            self.run_dry(recovery_args(self.base, old_target="other:main.0"))

    def test_reappearing_old_target_stops_new_replacement(self) -> None:
        args = recovery_args(self.base, new_target=True, dry_run=False)
        with (
            mock.patch.object(manager, "require_binary"),
            mock.patch.object(manager, "tmux_target_present", side_effect=[False, False, False, True]),
            mock.patch.object(manager, "unique_session_name", return_value="audit-ns-main-recovered-race"),
            mock.patch.object(manager, "codex_command", return_value="exec sleep 60"),
            mock.patch.object(manager, "start_tmux_target") as start_mock,
            mock.patch.object(manager, "stop_tmux_target") as stop_mock,
        ):
            with self.assertRaisesRegex(SystemExit, "stopped replacement"):
                manager.cmd_recover_coordinator(args)

        start_mock.assert_called_once()
        stop_mock.assert_called_once_with("audit-ns-main-recovered-race", "codex", independent_session=True)


class HealthSupervisorPolicyTests(unittest.TestCase):
    def test_context_full_uses_in_place_and_missing_uses_new_target(self) -> None:
        args = argparse.Namespace(session="audit-ns", dry_run=False, keep_old_main=False)
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(health, "run", return_value=completed) as run_mock:
            health.recover_coordinator(Path("/tmp/audit-state"), args, "audit-main:stable.0", "context-window-exhausted")
            context_cmd = run_mock.call_args.args[0]
            self.assertNotIn("--new-target", context_cmd)
            self.assertNotIn("--kill-old", context_cmd)

            health.recover_coordinator(Path("/tmp/audit-state"), args, "audit-main:stable.0", "coordinator-target-missing")
            missing_cmd = run_mock.call_args.args[0]
            self.assertIn("--new-target", missing_cmd)
            self.assertNotIn("--kill-old", missing_cmd)

    def test_target_alive_falls_back_when_exact_pane_display_is_unavailable(self) -> None:
        failed = subprocess.CompletedProcess([], 1, "", "missing")
        fallback = subprocess.CompletedProcess([], 0, f"{os.getpid()}\n", "")
        with mock.patch.object(health, "tmux", side_effect=[failed, fallback]) as tmux_mock:
            self.assertTrue(health.target_alive("audit-main:stable.0"))

        self.assertEqual(tmux_mock.call_args_list[0].args[:4], ("display-message", "-p", "-t", "audit-main:stable.0"))
        self.assertEqual(tmux_mock.call_args_list[1].args[:3], ("list-panes", "-t", "audit-main:stable.0"))

    def test_load_targets_excludes_stopped_and_keeps_missing_active_workers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-health-targets-") as temp:
            base = Path(temp)
            health.write_text(
                base / "workers.json",
                json.dumps(
                    {
                        "workers": {
                            "active-missing": {
                                "session": "definitely-missing-session",
                                "window": "codex",
                                "mode": "interactive",
                            },
                            "stopped-worker": {
                                "session": "also-missing-session",
                                "window": "codex",
                                "mode": "interactive",
                                "stopped_at": "2026-06-04T00:00:00+08:00",
                            },
                        }
                    }
                ),
            )
            args = argparse.Namespace(
                no_coordinator=True,
                no_workers=False,
                watch_target=None,
                observe_target=None,
            )
            targets = health.load_targets(base, "audit-ns", args)

        self.assertEqual([target["name"] for target in targets], ["active-missing"])
        self.assertEqual(targets[0]["target"], "definitely-missing-session:codex")

    def test_prune_loop_state_removes_closed_history_and_keeps_current_missing_target(self) -> None:
        loop_state = {
            "targets": {
                "active-missing@definitely-missing-session:codex": {"last_alive": False},
                "stopped-worker@also-missing-session:codex": {"last_alive": False},
            }
        }
        targets = [
            {
                "name": "active-missing",
                "target": "definitely-missing-session:codex",
            }
        ]

        removed = health.prune_loop_state(loop_state, targets)

        self.assertEqual(removed, ["stopped-worker@also-missing-session:codex"])
        self.assertEqual(
            loop_state["targets"],
            {"active-missing@definitely-missing-session:codex": {"last_alive": False}},
        )


class CoordinatorPeerMessageTests(unittest.TestCase):
    def test_peer_send_main_coordinator_alias_writes_inbox_and_notifies_registered_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-coordinator-peer-") as temp:
            base = Path(temp) / "state"
            source_progress = base / "progress" / "source-worker.md"
            manager.save_registry(
                base,
                {
                    "version": 1,
                    "session": "audit-ns",
                    "workers": {
                        "source-worker": {
                            "name": "source-worker",
                            "progress_file": str(source_progress),
                        }
                    },
                    "coordinator": {
                        "role": "main-coordinator",
                        "target": "audit-main:stable.0",
                        "cwd": temp,
                    },
                },
            )
            args = argparse.Namespace(
                state_dir=str(base),
                session="audit-ns",
                source="source-worker",
                target="main-coordinator",
                message="Terminal READY: results/report.json",
                message_file=None,
                notify=True,
                escape_first=False,
                escape_after=False,
            )
            with (
                mock.patch.object(manager, "tmux_target_present", return_value=True),
                mock.patch.object(manager, "send_prompt") as send_mock,
                mock.patch.object(manager, "refresh_schedule_doc"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                manager.cmd_peer_send(args)

            inbox_files = list(manager.coordinator_inbox_path(base).glob("*.md"))
            self.assertEqual(len(inbox_files), 1)
            self.assertIn("Terminal READY", inbox_files[0].read_text(encoding="utf-8"))
            send_mock.assert_called_once()
            self.assertEqual(send_mock.call_args.args[0], "audit-main:stable.0")
            self.assertIn("main-coordinator", manager.peer_messages_path(base).read_text(encoding="utf-8"))


class HistoricalRegistryStopTests(unittest.TestCase):
    def test_stop_resolves_exact_historical_key_before_safe_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="codex-stop-historical-") as temp:
            base = Path(temp) / "state"
            historical_key = "legacy-worker-"
            manager.save_registry(
                base,
                {
                    "version": 1,
                    "session": "audit-ns",
                    "workers": {
                        historical_key: {
                            "name": historical_key,
                            "session": "missing-legacy-session",
                            "window": "codex",
                            "session_mode": "independent",
                        }
                    },
                },
            )
            args = argparse.Namespace(state_dir=str(base), session="audit-ns", name=historical_key)
            with (
                mock.patch.object(manager, "session_exists", return_value=False),
                mock.patch.object(manager, "refresh_schedule_doc"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                manager.cmd_stop(args)

            registry = manager.load_registry(base)
            self.assertIn("stopped_at", registry["workers"][historical_key])
            self.assertNotIn("legacy-worker", registry["workers"])


@unittest.skipUnless(shutil.which("tmux"), "tmux is required for isolated real-tmux simulation")
class IsolatedTmuxRecoverySimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-recovery-tmux-")
        self.base = Path(self.temp.name) / "state"
        self.socket = f"codex-audit-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def tearDown(self) -> None:
        self.tmux("kill-server", check=False)
        self.temp.cleanup()

    def tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["tmux", "-L", self.socket, *args], text=True, capture_output=True, check=check)

    def pane_pid(self, target: str) -> int:
        result = self.tmux("display-message", "-p", "-t", target, "#{pane_pid}")
        return int(result.stdout.strip())

    def session_names(self) -> list[str]:
        result = self.tmux("list-sessions", "-F", "#{session_name}", check=False)
        if result.returncode != 0:
            return []
        return sorted(line for line in result.stdout.splitlines() if line)

    def window_option(self, target: str, option: str) -> str:
        result = self.tmux("show-window-options", "-v", "-t", target, option)
        return result.stdout.strip()

    @contextlib.contextmanager
    def patched_manager_runtime(self):
        with (
            mock.patch.object(manager, "tmux", self.tmux),
            mock.patch.object(manager, "require_binary"),
            mock.patch.object(manager, "codex_command", return_value="exec sleep 60"),
            mock.patch.object(manager, "send_prompt"),
            mock.patch.object(manager.time, "sleep"),
        ):
            yield

    def test_in_place_recovery_preserves_exact_target_and_creates_no_session(self) -> None:
        session = "audit-main-stable"
        target = f"{session}:main.0"
        self.tmux("new-session", "-d", "-s", session, "-n", "main", "-c", self.temp.name, "bash", "-lc", "exec sleep 60")
        write_registry(self.base, target, Path(self.temp.name))
        before_pid = self.pane_pid(target)

        with self.patched_manager_runtime(), contextlib.redirect_stdout(io.StringIO()):
            manager.cmd_recover_coordinator(recovery_args(self.base, dry_run=False))

        after_pid = self.pane_pid(target)
        self.assertNotEqual(before_pid, after_pid)
        self.assertEqual(self.session_names(), [session])
        registry = manager.load_registry(self.base)
        self.assertEqual(registry["coordinator"]["target"], target)
        self.assertEqual(registry["coordinator"]["last_recovery_policy"], "in-place")
        self.assertEqual(registry["coordinator"]["previous_targets"], [])
        self.assertEqual(self.window_option(target, "automatic-rename"), "off")
        self.assertEqual(self.window_option(target, "allow-rename"), "off")

    def test_register_coordinator_locks_window_name_against_tmux_auto_rename(self) -> None:
        session = "audit-register-stable"
        target = f"{session}:0.0"
        self.tmux("new-session", "-d", "-s", session, "-n", "node", "-c", self.temp.name, "bash", "-lc", "exec sleep 60")
        self.tmux("set-window-option", "-t", target, "automatic-rename", "on")
        self.assertEqual(self.window_option(target, "automatic-rename"), "on")
        args = argparse.Namespace(
            state_dir=str(self.base),
            session="audit-ns",
            shared_session=False,
            target=target,
            cwd=self.temp.name,
            mission="stable coordinator registration",
            restart_window_prefix="main-recovered",
            allow_missing=False,
            model=None,
            reasoning_effort=None,
            no_best_model=False,
            profile=None,
            sandbox="danger-full-access",
            approval="never",
            search=False,
        )

        with (
            mock.patch.object(manager, "tmux", self.tmux),
            mock.patch.object(manager, "require_binary"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            manager.cmd_register_coordinator(args)

        self.assertEqual(self.window_option(target, "automatic-rename"), "off")
        self.assertEqual(self.window_option(target, "allow-rename"), "off")
        registry = manager.load_registry(self.base)
        self.assertEqual(registry["coordinator"]["target"], target)

    def test_health_target_alive_accepts_exact_session_window_pane(self) -> None:
        session = "audit-health-exact-pane"
        target = f"{session}:node.0"
        self.tmux("new-session", "-d", "-s", session, "-n", "node", "-c", self.temp.name, "bash", "-lc", "exec sleep 60")

        with mock.patch.object(health, "tmux", self.tmux), mock.patch.object(manager, "tmux", self.tmux):
            self.assertTrue(health.target_alive(target))
            self.assertTrue(manager.tmux_target_present(target))

    def test_start_target_locks_long_lived_window_and_tolerates_short_lived_exec(self) -> None:
        long_session = "audit-managed-long"
        long_target = f"{long_session}:codex"
        short_session = "audit-managed-short"

        with mock.patch.object(manager, "tmux", self.tmux):
            manager.start_tmux_target(
                long_session,
                "codex",
                Path(self.temp.name),
                "exec sleep 60",
                independent_session=True,
            )
            manager.start_tmux_target(
                short_session,
                "exec",
                Path(self.temp.name),
                "exit 0",
                independent_session=True,
            )

        self.assertEqual(self.window_option(long_target, "automatic-rename"), "off")
        self.assertEqual(self.window_option(long_target, "allow-rename"), "off")

    def test_missing_target_fails_closed_then_explicitly_allocates_one_replacement(self) -> None:
        old_session = "audit-main-missing"
        old_target = f"{old_session}:main.0"
        self.tmux("new-session", "-d", "-s", old_session, "-n", "main", "-c", self.temp.name, "bash", "-lc", "exec sleep 60")
        write_registry(self.base, old_target, Path(self.temp.name))
        self.tmux("kill-session", "-t", old_session)

        with self.patched_manager_runtime():
            with self.assertRaisesRegex(SystemExit, "fails closed by default"):
                manager.cmd_recover_coordinator(recovery_args(self.base, dry_run=False))
        self.assertEqual(self.session_names(), [])

        with self.patched_manager_runtime(), contextlib.redirect_stdout(io.StringIO()):
            manager.cmd_recover_coordinator(recovery_args(self.base, new_target=True, dry_run=False))

        sessions = self.session_names()
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0].startswith("audit-ns-main-recovered-"))
        registry = manager.load_registry(self.base)
        self.assertNotEqual(registry["coordinator"]["target"], old_target)
        self.assertEqual(registry["coordinator"]["last_recovery_policy"], "new-target")


if __name__ == "__main__":
    unittest.main(verbosity=2)
