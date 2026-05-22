#!/usr/bin/env python3
"""Health supervisor for tmux-hosted Codex panes.

This script is intentionally separate from codex_tmux_manager.py's normal
supervisor loop. The normal supervisor captures progress and optional status
queries; this health supervisor focuses on Codex panes that are visibly stuck
on recoverable transport/subprocess errors.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_SESSION = "cw"
DEFAULT_STATE_DIR = ".codex/tmux-workers"
MANAGER_PATH = Path(__file__).resolve().with_name("codex_tmux_manager.py")
DEFAULT_ERROR_PATTERNS = [
    "stream disconnected before completion",
    "timeout waiting for child process to exit",
    "connection closed before message completed",
    "error sending request",
    "network error",
    "connection reset",
    "ECONNRESET",
    "ETIMEDOUT",
    "503 Service Unavailable",
    "502 Bad Gateway",
]
DEFAULT_FATAL_CONTEXT_PATTERNS = [
    r"Codex ran out of room in the model's context window",
    r"Start a new thread or clear earlier history before retrying",
]
ACTIVE_PATTERNS = [
    r"\bWorking\b",
    r"\brunning\b",
    r"\bSearching the web\b",
]
DEFAULT_RECOVERY_PROMPT = (
    "The coordinator health supervisor detected that this Codex pane appears to "
    "be stopped on a recoverable network or child-process error. Please continue "
    "the previous autonomous task from the latest progress/report/inbox state. "
    "Do not restart from scratch; first record the recovery in your progress file."
)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["tmux", *args], check=check)


def state_dir(path: str | None) -> Path:
    return Path(path or DEFAULT_STATE_DIR).expanduser().resolve()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    append_text(path, json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def log(base: Path, level: str, message: str) -> None:
    line = f"{now_iso()} [{level}] {message}\n"
    append_text(base / "logs" / "health-supervisor.log", line)
    print(line, end="", flush=True)


def capture_target(target: str, lines: int) -> str:
    result = tmux("capture-pane", "-p", "-S", f"-{lines}", "-t", target, check=False)
    return result.stdout.rstrip() if result.returncode == 0 else ""


def target_alive(target: str) -> bool:
    result = tmux("list-panes", "-t", target, "-F", "#{pane_pid}", check=False)
    if result.returncode != 0:
        return False
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if not raw.isdigit():
            continue
        try:
            os.kill(int(raw), 0)
            return True
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
    return False


def send_prompt(target: str, message: str, *, escape_after: bool = False) -> None:
    tmux("set-buffer", message)
    tmux("paste-buffer", "-t", target)
    time.sleep(0.2)
    tmux("send-keys", "-t", target, "C-m")
    if escape_after:
        time.sleep(0.5)
        tmux("send-keys", "-t", target, "Escape")


def parse_named_target(raw: str, *, recover: bool) -> dict[str, Any]:
    if "=" not in raw:
        raise SystemExit(f"Target must be NAME=TMUX_TARGET: {raw}")
    name, target = raw.split("=", 1)
    name = name.strip()
    target = target.strip()
    if not name or not target:
        raise SystemExit(f"Target must be NAME=TMUX_TARGET: {raw}")
    return {
        "name": name,
        "target": target,
        "source": "extra",
        "mode": "interactive" if recover else "observe",
        "recover": recover,
    }


def load_targets(base: Path, session: str, args: argparse.Namespace) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    registry = read_json(base / "workers.json", {"workers": {}})

    if not args.no_coordinator:
        coordinator = registry.get("coordinator") or {}
        target = coordinator.get("target")
        if target and not coordinator.get("stopped_at"):
            item = {
                "name": "main",
                "target": target,
                "source": "coordinator-registry",
                "mode": "coordinator",
                "recover": True,
                "coordinator": True,
            }
            key = (item["name"], item["target"])
            if key not in seen:
                targets.append(item)
                seen.add(key)

    if not args.no_workers:
        for name, worker in sorted(registry.get("workers", {}).items()):
            if worker.get("stopped_at"):
                continue
            window = worker.get("window")
            worker_session = worker.get("session", session)
            if not window:
                continue
            mode = str(worker.get("mode", ""))
            target = f"{worker_session}:{window}"
            recover = mode == "interactive"
            item = {
                "name": name,
                "target": target,
                "source": "worker-registry",
                "mode": mode or "unknown",
                "recover": recover,
                "progress_file": worker.get("progress_file"),
                "status_file": worker.get("status_file"),
            }
            key = (item["name"], item["target"])
            if key not in seen:
                targets.append(item)
                seen.add(key)

    for raw in args.watch_target or []:
        item = parse_named_target(raw, recover=True)
        key = (item["name"], item["target"])
        if key not in seen:
            targets.append(item)
            seen.add(key)

    for raw in args.observe_target or []:
        item = parse_named_target(raw, recover=False)
        key = (item["name"], item["target"])
        if key not in seen:
            targets.append(item)
            seen.add(key)

    return targets


def compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def find_last_error(lines: list[str], patterns: list[re.Pattern[str]]) -> tuple[int, str] | None:
    found: tuple[int, str] | None = None
    for index, line in enumerate(lines):
        for pattern in patterns:
            if pattern.search(line):
                found = (index, pattern.pattern)
                break
    return found


def has_active_after(lines: list[str], index: int) -> bool:
    tail = "\n".join(lines[index + 1 :])
    return any(re.search(pattern, tail, flags=re.IGNORECASE) for pattern in ACTIVE_PATTERNS)


def classify_capture(text: str, patterns: list[re.Pattern[str]], fatal_patterns: list[re.Pattern[str]]) -> dict[str, Any]:
    lines = text.splitlines()
    fatal_found = find_last_error(lines, fatal_patterns)
    if fatal_found:
        index, pattern = fatal_found
        if not has_active_after(lines, index):
            signature_source = "\n".join(lines[max(0, index - 2) : index + 3])
            signature = hashlib.sha256(signature_source.encode("utf-8", errors="replace")).hexdigest()[:16]
            return {
                "stuck": True,
                "fatal": "context-full",
                "reason": "context-window-exhausted",
                "pattern": pattern,
                "signature": signature,
                "line": lines[index],
            }
    found = find_last_error(lines, patterns)
    if not found:
        return {"stuck": False, "reason": "no-error"}
    index, pattern = found
    if has_active_after(lines, index):
        return {"stuck": False, "reason": "active-after-error", "pattern": pattern}
    signature_source = "\n".join(lines[max(0, index - 2) : index + 3])
    signature = hashlib.sha256(signature_source.encode("utf-8", errors="replace")).hexdigest()[:16]
    return {
        "stuck": True,
        "reason": "recoverable-error-at-tail",
        "pattern": pattern,
        "signature": signature,
        "line": lines[index],
    }


def load_loop_state(base: Path) -> dict[str, Any]:
    return read_json(base / "status" / "health_supervisor_state.json", {"targets": {}})


def save_loop_state(base: Path, data: dict[str, Any]) -> None:
    write_text(base / "status" / "health_supervisor_state.json", json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def write_status(base: Path, status: dict[str, Any]) -> None:
    write_text(base / "status" / "health_supervisor.json", json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def append_schedule_event(base: Path, event: str, detail: str, target_name: str = "") -> None:
    append_jsonl(
        base / "schedule_events.jsonl",
        {
            "timestamp": now_iso(),
            "event": event,
            "worker": target_name,
            "detail": detail,
        },
    )


def recover_coordinator(base: Path, args: argparse.Namespace, target: str, reason: str) -> subprocess.CompletedProcess[str] | None:
    cmd = [
        sys.executable,
        str(MANAGER_PATH),
        "--state-dir",
        str(base),
        "--session",
        args.session,
        "recover-coordinator",
        "--reason",
        reason,
        "--old-target",
        target,
    ]
    if not args.keep_old_main:
        cmd.append("--kill-old")
    if args.dry_run:
        return None
    return run(cmd, check=False)


def supervise_target(
    base: Path,
    target_info: dict[str, Any],
    args: argparse.Namespace,
    patterns: list[re.Pattern[str]],
    fatal_patterns: list[re.Pattern[str]],
    loop_state: dict[str, Any],
) -> dict[str, Any]:
    name = target_info["name"]
    target = target_info["target"]
    target_key = f"{name}@{target}"
    state = loop_state.setdefault("targets", {}).setdefault(target_key, {})
    target_status: dict[str, Any] = {
        "name": name,
        "target": target,
        "source": target_info.get("source", ""),
        "mode": target_info.get("mode", ""),
        "recover": bool(target_info.get("recover")),
        "coordinator": bool(target_info.get("coordinator")),
        "checked_at": now_iso(),
    }

    if not target_alive(target):
        state["last_alive"] = False
        target_status.update({"state": "not-present", "action": "none"})
        log(base, "WARN", f"{name}: target not present or pane process dead: {target}")
        if target_info.get("coordinator") and args.restart_main_when_missing:
            now_ts = time.time()
            last_recovery_ts = float(state.get("last_missing_recovery_ts", 0))
            cooldown_left = max(0, args.cooldown - int(now_ts - last_recovery_ts))
            if cooldown_left > 0:
                target_status["action"] = "missing-cooldown"
                target_status["cooldown_left_seconds"] = cooldown_left
                log(base, "INFO", f"{name}: coordinator target missing but cooldown has {cooldown_left}s left")
                return target_status
            state["last_missing_recovery_ts"] = now_ts
            state["last_missing_recovery_at"] = now_iso()
            state["missing_recovery_count"] = int(state.get("missing_recovery_count", 0)) + 1
            target_status["action"] = "dry-run-recover-coordinator-missing" if args.dry_run else "recover-coordinator-missing"
            log(base, "ERROR", f"{name}: coordinator target missing; action={target_status['action']} target={target}")
            append_schedule_event(base, "coordinator-missing-recovery", f"{target} dry_run={args.dry_run} keep_old={args.keep_old_main}", name)
            result = recover_coordinator(base, args, target, "coordinator-target-missing")
            if result is not None:
                target_status["recover_exit_code"] = result.returncode
                if result.stdout.strip():
                    log(base, "INFO", f"{name}: recover-coordinator stdout: {result.stdout.strip()}")
                if result.stderr.strip():
                    log(base, "WARN", f"{name}: recover-coordinator stderr: {result.stderr.strip()}")
        return target_status

    state["last_alive"] = True
    captured = capture_target(target, args.lines)
    capture_hash = hashlib.sha256(captured.encode("utf-8", errors="replace")).hexdigest()
    now_ts = time.time()
    if state.get("capture_hash") != capture_hash:
        state["capture_hash"] = capture_hash
        state["last_change_ts"] = now_ts
        state["last_change_at"] = now_iso()
    last_change_ts = float(state.get("last_change_ts", now_ts))
    stable_seconds = int(max(0, now_ts - last_change_ts))
    health = classify_capture(captured, patterns, fatal_patterns)
    target_status.update(
        {
            "state": "fatal-context-full" if health.get("fatal") == "context-full" else ("stuck-error" if health.get("stuck") else "ok"),
            "reason": health.get("reason"),
            "stable_seconds": stable_seconds,
            "error_pattern": health.get("pattern"),
            "error_line": health.get("line"),
        }
    )

    if not health.get("stuck"):
        state.pop("active_error_signature", None)
        state.pop("first_error_ts", None)
        state.pop("first_error_at", None)
        target_status["action"] = "none"
        return target_status

    signature = str(health.get("signature"))
    if state.get("active_error_signature") != signature:
        state["active_error_signature"] = signature
        state["first_error_ts"] = now_ts
        state["first_error_at"] = now_iso()
    first_error_ts = float(state.get("first_error_ts", now_ts))
    error_age = int(max(0, now_ts - first_error_ts))
    last_recovery_ts = float(state.get("last_recovery_ts", 0))
    cooldown_left = max(0, args.cooldown - int(now_ts - last_recovery_ts))
    target_status["error_age_seconds"] = error_age

    if stable_seconds < args.stable_seconds and error_age < args.stable_seconds:
        target_status["action"] = "wait-stability"
        log(base, "INFO", f"{name}: recoverable error seen but waiting for stability ({stable_seconds}s stable, {error_age}s age)")
        return target_status

    if cooldown_left > 0:
        target_status["action"] = "cooldown"
        target_status["cooldown_left_seconds"] = cooldown_left
        log(base, "INFO", f"{name}: recoverable error still present but cooldown has {cooldown_left}s left")
        return target_status

    if health.get("fatal") == "context-full":
        if not target_info.get("coordinator"):
            target_status["action"] = "observe-fatal-context-full"
            log(base, "ERROR", f"{name}: context-window exhaustion detected in non-coordinator target {target}; manual restart required")
            append_schedule_event(base, "fatal-context-full", f"{target} pattern={health.get('pattern')} non_coordinator=true", name)
            return target_status
        if not args.restart_main_on_context_full:
            target_status["action"] = "manual-coordinator-recovery-required"
            log(base, "ERROR", f"{name}: coordinator context exhausted at {target}; restart disabled, run recover-coordinator manually")
            append_schedule_event(base, "coordinator-context-full", f"{target} restart_disabled=true pattern={health.get('pattern')}", name)
            return target_status
        state["last_recovery_ts"] = now_ts
        state["last_recovery_at"] = now_iso()
        state["recovery_count"] = int(state.get("recovery_count", 0)) + 1
        target_status["action"] = "dry-run-recover-coordinator" if args.dry_run else "recover-coordinator"
        log(base, "ERROR", f"{name}: coordinator context exhausted; action={target_status['action']} target={target}")
        append_schedule_event(base, "coordinator-context-recovery", f"{target} dry_run={args.dry_run} keep_old={args.keep_old_main}", name)
        if not args.dry_run:
            result = recover_coordinator(base, args, target, "context-window-exhausted")
            target_status["recover_exit_code"] = result.returncode
            if result.stdout.strip():
                log(base, "INFO", f"{name}: recover-coordinator stdout: {result.stdout.strip()}")
            if result.stderr.strip():
                log(base, "WARN", f"{name}: recover-coordinator stderr: {result.stderr.strip()}")
        return target_status

    if not target_info.get("recover"):
        target_status["action"] = "observe-only"
        log(base, "WARN", f"{name}: recoverable error detected in observe-only target {target}: {health.get('line')}")
        return target_status

    state["last_recovery_ts"] = now_ts
    state["last_recovery_at"] = now_iso()
    state["recovery_count"] = int(state.get("recovery_count", 0)) + 1
    target_status["action"] = "dry-run-recover" if args.dry_run else "recover"
    message = args.recovery_prompt
    log(base, "WARN", f"{name}: recoverable error detected; action={target_status['action']} target={target} pattern={health.get('pattern')}")
    append_schedule_event(base, "health-recovery", f"{target} pattern={health.get('pattern')} dry_run={args.dry_run}", name)
    if not args.dry_run:
        send_prompt(target, message, escape_after=args.escape_after)
    if target_info.get("progress_file"):
        append_text(
            Path(target_info["progress_file"]),
            "\n## Health Supervisor Recovery - "
            + now_iso()
            + "\n\n"
            + f"- Target: {target}\n"
            + f"- Pattern: {health.get('pattern')}\n"
            + f"- Action: {target_status['action']}\n",
        )
    return target_status


def supervise_once(base: Path, args: argparse.Namespace, loop_state: dict[str, Any]) -> list[dict[str, Any]]:
    patterns = compile_patterns(DEFAULT_ERROR_PATTERNS + (args.error_pattern or []))
    fatal_patterns = compile_patterns(DEFAULT_FATAL_CONTEXT_PATTERNS + (args.fatal_context_pattern or []))
    targets = load_targets(base, args.session, args)
    statuses = []
    if not targets:
        log(base, "INFO", "no targets to supervise")
    for target_info in targets:
        statuses.append(supervise_target(base, target_info, args, patterns, fatal_patterns, loop_state))
    return statuses


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor tmux Codex panes for recoverable stuck errors.")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--lines", type=int, default=40)
    parser.add_argument("--stable-seconds", type=int, default=20)
    parser.add_argument("--cooldown", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-foreground-loop", action="store_true", help="Allow loop mode outside a managed tmux target.")
    parser.add_argument("--no-workers", action="store_true", help="Do not monitor workers from workers.json.")
    parser.add_argument("--no-coordinator", action="store_true", help="Do not monitor the registered main coordinator target from workers.json.")
    parser.add_argument("--watch-target", action="append", help="Extra interactive Codex target to auto-recover, as NAME=TMUX_TARGET.")
    parser.add_argument("--observe-target", action="append", help="Extra target to observe without auto-recovery, as NAME=TMUX_TARGET.")
    parser.add_argument("--error-pattern", action="append", help="Additional case-insensitive regex treated as recoverable.")
    parser.add_argument("--fatal-context-pattern", action="append", help="Additional case-insensitive regex treated as coordinator context exhaustion.")
    parser.add_argument("--restart-main-on-context-full", action="store_true", help="Auto-launch recover-coordinator when the registered main coordinator exhausts context.")
    parser.add_argument("--restart-main-when-missing", action="store_true", help="Auto-launch recover-coordinator when the registered main coordinator target disappears.")
    parser.add_argument("--keep-old-main", action="store_true", help="Do not kill the old coordinator pane during main-coordinator auto-recovery.")
    parser.add_argument("--recovery-prompt", default=DEFAULT_RECOVERY_PROMPT)
    parser.add_argument("--escape-after", action="store_true", help="Send Escape after submitting the recovery prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Detect and log recoveries without sending prompts.")
    args = parser.parse_args()

    if not args.once and not args.allow_foreground_loop and not os.environ.get("CODEX_HEALTH_SUPERVISOR_MANAGED"):
        raise SystemExit(
            "Refusing to run an unbounded health-supervisor loop in the foreground. "
            "Use codex_tmux_manager.py start-health-supervisor, or pass --allow-foreground-loop only inside tmux."
        )

    base = state_dir(args.state_dir)
    loop_state = load_loop_state(base)
    started_at = now_iso()
    log(base, "INFO", f"health supervisor started session={args.session} interval={args.interval}s dry_run={args.dry_run}")
    cycle = 0
    last_statuses: list[dict[str, Any]] = []
    try:
        while True:
            cycle += 1
            statuses = supervise_once(base, args, loop_state)
            last_statuses = statuses
            save_loop_state(base, loop_state)
            write_status(
                base,
                {
                    "state": "running",
                    "pid": os.getpid(),
                    "started_at": started_at,
                    "last_loop_at": now_iso(),
                    "cycle": cycle,
                    "session": args.session,
                    "interval": args.interval,
                    "targets": statuses,
                    "dry_run": args.dry_run,
                },
            )
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        write_status(
            base,
            {
                "state": "completed-once" if args.once else "exited",
                "pid": os.getpid(),
                "started_at": started_at,
                "exited_at": now_iso(),
                "cycle": cycle,
                "targets": last_statuses,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
