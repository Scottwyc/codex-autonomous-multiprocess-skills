#!/usr/bin/env python3
"""Manage Codex workers running in tmux windows."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_SESSION = "codex-workers"
DEFAULT_STATE_DIR = ".codex/tmux-workers"
DEFAULT_WORKER_MODEL = os.environ.get("CODEX_WORKER_DEFAULT_MODEL", "gpt-5.5")
DEFAULT_WORKER_REASONING = os.environ.get("CODEX_WORKER_DEFAULT_REASONING", "xhigh")
MANAGER_PATH = Path(__file__).resolve()
HEALTH_SUPERVISOR_PATH = MANAGER_PATH.with_name("codex_tmux_health_supervisor.py")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["tmux", *args], check=check)


def require_binary(name: str) -> None:
    if run(["bash", "-lc", f"command -v {shlex.quote(name)}"], check=False).returncode != 0:
        raise SystemExit(f"Required binary not found: {name}")


def git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], check=check)


def safe_name(name: str) -> str:
    lowered = name.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_.-]+", "-", lowered).strip("-")
    if not cleaned:
        raise SystemExit("Worker name must contain at least one alphanumeric character.")
    return cleaned[:48]


def state_dir(path: str | None) -> Path:
    return Path(path or DEFAULT_STATE_DIR).expanduser().resolve()


def registry_path(base: Path) -> Path:
    return base / "workers.json"


def schedule_doc_path(base: Path) -> Path:
    return base / "COORDINATOR_SCHEDULE.md"


def schedule_events_path(base: Path) -> Path:
    return base / "schedule_events.jsonl"


def peer_messages_path(base: Path) -> Path:
    return base / "peer_messages.jsonl"


def consult_dir_path(base: Path) -> Path:
    return base / "consult"


def consult_context_path(base: Path) -> Path:
    return consult_dir_path(base) / "CONSULT_CONTEXT.md"


def consult_prompt_path(base: Path) -> Path:
    return consult_dir_path(base) / "consult.prompt.md"


def consult_status_path(base: Path) -> Path:
    return consult_dir_path(base) / "consult.status.json"


def consult_log_path(base: Path) -> Path:
    return base / "logs" / "consult.log"


def coordinator_handoff_path(base: Path) -> Path:
    return base / "COORDINATOR_RECOVERY.md"


def coordinator_memory_path(base: Path) -> Path:
    return base / "COORDINATOR_MEMORY.md"


def coordinator_context_pack_path(base: Path) -> Path:
    return base / "COORDINATOR_CONTEXT_PACK.md"


def coordinator_memory_events_path(base: Path) -> Path:
    return base / "coordinator_memory_events.jsonl"


def coordinator_constraints_path(base: Path) -> Path:
    return base / "COORDINATOR_CONSTRAINTS.md"


def coordinator_constraints_events_path(base: Path) -> Path:
    return base / "coordinator_constraints_events.jsonl"


def coordinator_status_path(base: Path) -> Path:
    return base / "status" / "coordinator.json"


def coordinator_log_path(base: Path) -> Path:
    return base / "logs" / "coordinator.log"


def supervisor_status_path(base: Path) -> Path:
    return base / "status" / "supervisor.json"


def load_registry(base: Path) -> dict[str, Any]:
    path = registry_path(base)
    if not path.exists():
        return {"version": 1, "workers": {}}
    for attempt in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if attempt == 2:
                raise
            time.sleep(0.1)
    return {"version": 1, "workers": {}}


def save_registry(base: Path, registry: dict[str, Any]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    path = registry_path(base)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_manager_log(base: Path, line: str) -> None:
    base.mkdir(parents=True, exist_ok=True)
    with (base / "manager.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{now_iso()} {line}\n")


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def one_line(text: str, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def resolve_model_settings(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if getattr(args, "no_best_model", False):
        return args.model, args.reasoning_effort
    model = args.model or DEFAULT_WORKER_MODEL
    reasoning = args.reasoning_effort or DEFAULT_WORKER_REASONING
    return model, reasoning


def tail_text(path: Path, lines: int) -> str:
    if not path.is_file():
        return "missing"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def extract_markdown_section(path: Path, heading: str, limit: int = 500) -> str:
    if not path.is_file():
        return "missing"
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(rf"^## {re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return one_line(text, limit)
    rest = text[match.end() :]
    next_heading = re.search(r"^##\s+", rest, re.MULTILINE)
    section = rest[: next_heading.start()] if next_heading else rest
    return one_line(section, limit)


def append_schedule_event(
    base: Path,
    event: str,
    *,
    worker: str | None = None,
    detail: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    record = {
        "timestamp": now_iso(),
        "event": event,
        "worker": worker or "",
        "detail": detail,
        "data": data or {},
    }
    append_text(schedule_events_path(base), json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_schedule_events(base: Path, limit: int = 80) -> list[dict[str, Any]]:
    path = schedule_events_path(base)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_peer_messages(base: Path, limit: int = 40) -> list[dict[str, Any]]:
    path = peer_messages_path(base)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def append_memory_event(
    base: Path,
    event: str,
    *,
    note: str = "",
    decision: str = "",
    next_action: str = "",
    reason: str = "",
) -> None:
    record = {
        "timestamp": now_iso(),
        "event": event,
        "reason": reason,
        "note": note,
        "decision": decision,
        "next_action": next_action,
    }
    append_text(coordinator_memory_events_path(base), json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load_memory_events(base: Path, limit: int = 40) -> list[dict[str, Any]]:
    path = coordinator_memory_events_path(base)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def default_constraints_text() -> str:
    return f"""# Coordinator Unified Constraints

Updated: {now_iso()}

These constraints are set by the main coordinator and apply to every tmux-launched Codex process unless the coordinator explicitly records an override with `constraints` and `schedule-note`.

## Priority

1. Obey task-specific safety and write-scope limits.
2. Obey these coordinator-wide constraints.
3. Then follow the worker-specific task plan.

If a task conflicts with this file, stop and ask the coordinator for a recorded override instead of improvising.

## Default Operational Constraints

- Read this file before starting or resuming work, before launching child workers, and before starting background jobs.
- Keep coordinator-facing updates concise; write long logs, tables, diffs, TensorBoard output, and transcripts to files and cite paths.
- Use explicit `--resource` ownership for GPUs, ports, output directories, checkpoints, TensorBoard instances, and long-running jobs.
- Do not start duplicate training/evaluation/TensorBoard jobs for the same owned output without checking `jobs`, progress, and schedule first.
- Do not bind dashboards or services to `0.0.0.0` unless the coordinator explicitly authorizes it. Prefer `127.0.0.1`.
- TensorBoard and similar dashboards must use coordinator-assigned safe ports. Prefer the project-local range `16006-16099` when no project-specific range is recorded, and register the port as `--resource port:<PORT>` or in the job/progress record.
- If a desired port is already in use, do not kill the owner blindly. Pick another allowed port or ask the coordinator to resolve ownership.
- Destructive cleanup such as `rm -rf`, deleting checkpoints, killing unrelated processes, or overwriting shared results requires an explicit coordinator decision.
- Remote jobs must record host, tmux/session or PID marker, GPU, command, log path, output roots, and liveness check in progress/report because local PID tracking may not cover remote processes.
"""


def ensure_constraints_doc(base: Path) -> Path:
    path = coordinator_constraints_path(base)
    if not path.exists():
        write_text(path, default_constraints_text())
        append_text(
            coordinator_constraints_events_path(base),
            json.dumps({"timestamp": now_iso(), "event": "init-default-constraints", "detail": str(path)}, ensure_ascii=False, sort_keys=True) + "\n",
        )
    return path


def append_constraints_event(base: Path, event: str, detail: str, data: dict[str, Any] | None = None) -> None:
    append_text(
        coordinator_constraints_events_path(base),
        json.dumps(
            {
                "timestamp": now_iso(),
                "event": event,
                "detail": detail,
                "data": data or {},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )


def constraints_excerpt(base: Path, lines: int = 40) -> str:
    path = ensure_constraints_doc(base)
    return tail_text(path, lines)


def worker_latest_summary(worker: dict[str, Any], *, progress_lines: int = 5, report_lines: int = 5, limit: int = 220) -> str:
    progress = one_line(tail_text(Path(worker.get("progress_file", "")), progress_lines), limit)
    report = one_line(tail_text(Path(worker.get("report_file", "")), report_lines), limit)
    if progress == "missing" and report == "missing":
        return "missing progress/report"
    if report != "missing" and "Pending" not in report:
        return report
    return progress


def render_coordinator_memory(
    base: Path,
    registry: dict[str, Any],
    *,
    reason: str = "refresh",
    progress_lines: int = 5,
    report_lines: int = 5,
    event_limit: int = 14,
    peer_limit: int = 10,
    memory_event_limit: int = 12,
) -> str:
    workers = registry.get("workers", {})
    coordinator = registry.get("coordinator") or {}
    mission = registry.get("mission", "未设置")
    session = registry.get("session", DEFAULT_SESSION)
    active_rows: list[list[str]] = []
    resource_rows: list[list[str]] = []
    path_rows: list[list[str]] = []
    for name, worker in sorted(workers.items()):
        state = effective_state(worker, session)
        if state == "stopped":
            continue
        active_rows.append(
            [
                name,
                state,
                worker.get("worker_kind", "standard"),
                worker.get("parent_worker") or "main",
                ", ".join(worker.get("resources", [])) or "-",
                worker_latest_summary(worker, progress_lines=progress_lines, report_lines=report_lines, limit=220),
            ]
        )
        resources = ", ".join(worker.get("resources", [])) or "-"
        owned = ", ".join(worker.get("owned_paths", [])) or "-"
        resource_rows.append([name, resources, owned])
        path_rows.append(
            [
                name,
                worker.get("progress_file", "-"),
                worker.get("report_file", "-"),
                worker.get("jobs_file", "-"),
            ]
        )

    memory_events = load_memory_events(base, memory_event_limit)
    schedule_events = load_schedule_events(base, event_limit)
    peer_messages = load_peer_messages(base, peer_limit)
    lines = [
        "# Coordinator Compact Memory",
        "",
        f"Updated: {now_iso()}",
        f"Reason: {reason}",
        f"State dir: `{base}`",
        f"Mission: {mission}",
        f"Unified constraints: `{coordinator_constraints_path(base)}`",
        "",
        "This file is the coordinator's short working memory. Prefer reading it before larger schedule, collect, capture, log, or report files.",
        "",
        "## Context Discipline",
        "",
        "- Use this compact memory and `COORDINATOR_CONTEXT_PACK.md` as the first source after every monitoring checkpoint or thread restart.",
        "- Do not paste raw logs, full diffs, full reports, long tables, or full tmux transcripts into the main coordinator context.",
        "- Escalate from compact memory -> schedule -> progress/report tails -> short capture -> full artifact only when needed.",
        "- After meaningful decisions, run `compact-memory --note ... --decision ... --next-action ...` so the next coordinator does not rely on chat history.",
        "",
        "## Coordinator",
        "",
    ]
    if coordinator:
        target = coordinator.get("target", "")
        lines.extend(
            [
                f"- target: `{target or '-'}`",
                f"- target_state: `{'running' if target and tmux_target_present(target) else 'not-present'}`",
                f"- cwd: `{coordinator.get('cwd', '-')}`",
                f"- model/reasoning: `{coordinator.get('model', '-')}/{coordinator.get('reasoning_effort', '-')}`",
                f"- recovery handoff: `{coordinator_handoff_path(base)}`",
            ]
        )
    else:
        lines.append("- coordinator target not registered")

    lines.extend(["", "## Active Worker Snapshot", ""])
    lines.append(markdown_table(["Worker", "状态", "类型", "上级", "资源", "最新短摘要"], active_rows) if active_rows else "暂无 active worker。")
    lines.extend(["", "## Recent Coordinator Memory Notes", ""])
    if memory_events:
        lines.append(
            markdown_table(
                ["时间", "事件", "原因", "笔记", "决策", "下一步"],
                [
                    [
                        item.get("timestamp", ""),
                        item.get("event", ""),
                        item.get("reason", ""),
                        one_line(item.get("note", ""), 120),
                        one_line(item.get("decision", ""), 120),
                        one_line(item.get("next_action", ""), 120),
                    ]
                    for item in memory_events
                ],
            )
        )
    else:
        lines.append("暂无主进程压缩记忆笔记。")

    lines.extend(["", "## Recent Schedule Decisions", ""])
    if schedule_events:
        lines.append(
            markdown_table(
                ["时间", "事件", "Worker", "说明"],
                [[event.get("timestamp", ""), event.get("event", ""), event.get("worker", ""), one_line(event.get("detail", ""), 160)] for event in schedule_events],
            )
        )
    else:
        lines.append("暂无调度事件。")

    lines.extend(["", "## Recent Peer Messages", ""])
    if peer_messages:
        lines.append(
            markdown_table(
                ["时间", "来源", "目标", "摘要"],
                [[item.get("timestamp", ""), item.get("source", ""), item.get("target", ""), one_line(item.get("summary", ""), 140)] for item in peer_messages],
            )
        )
    else:
        lines.append("暂无 worker 横向消息。")

    lines.extend(["", "## Resource And Ownership Snapshot", ""])
    lines.append(markdown_table(["Worker", "资源", "Owned paths"], resource_rows) if resource_rows else "暂无资源声明。")

    lines.extend(["", "## Unified Constraints Excerpt", "", "```text", constraints_excerpt(base, 28), "```"])

    lines.extend(["", "## Evidence Pointers", ""])
    lines.append(markdown_table(["Worker", "Progress", "Report", "Jobs"], path_rows) if path_rows else "暂无 worker evidence。")

    lines.extend(
        [
            "",
            "## First Commands For The Coordinator",
            "",
            "```bash",
            f"python {MANAGER_PATH} --state-dir {base} compact-memory --print --context-pack",
            f"python {MANAGER_PATH} --state-dir {base} list",
            f"python {MANAGER_PATH} --state-dir {base} jobs",
            f"python {MANAGER_PATH} --state-dir {base} progress --lines 20",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_coordinator_context_pack(base: Path, registry: dict[str, Any], *, reason: str = "refresh") -> str:
    workers = registry.get("workers", {})
    coordinator = registry.get("coordinator") or {}
    mission = registry.get("mission", "未设置")
    session = registry.get("session", DEFAULT_SESSION)
    rows = []
    for name, worker in sorted(workers.items()):
        state = effective_state(worker, session)
        if state == "stopped":
            continue
        rows.append(
            [
                name,
                state,
                worker.get("worker_kind", "standard"),
                worker.get("parent_worker") or "main",
                ", ".join(worker.get("resources", [])) or "-",
                one_line(worker_latest_summary(worker, progress_lines=3, report_lines=3, limit=140), 140),
            ]
        )
    latest_memory = load_memory_events(base, 5)
    lines = [
        "# Coordinator Context Pack",
        "",
        f"Updated: {now_iso()}",
        f"Reason: {reason}",
        f"Mission: {mission}",
        f"State dir: `{base}`",
        f"Coordinator target: `{coordinator.get('target', '-') if coordinator else '-'}`",
        f"Unified constraints: `{coordinator_constraints_path(base)}`",
        "",
        "Use this as the short reload packet when the coordinator context is getting large. Read larger files only by path when needed.",
        "",
        "## Active Workers",
        "",
    ]
    lines.append(markdown_table(["Worker", "状态", "类型", "上级", "资源", "短摘要"], rows) if rows else "暂无 active worker。")
    lines.extend(["", "## Latest Memory Notes", ""])
    if latest_memory:
        for item in latest_memory:
            pieces = [item.get("timestamp", ""), item.get("event", "")]
            if item.get("decision"):
                pieces.append("decision=" + one_line(item.get("decision", ""), 100))
            if item.get("next_action"):
                pieces.append("next=" + one_line(item.get("next_action", ""), 100))
            if item.get("note"):
                pieces.append("note=" + one_line(item.get("note", ""), 100))
            lines.append("- " + " | ".join(part for part in pieces if part))
    else:
        lines.append("- No compact memory notes yet.")
    lines.extend(
        [
            "",
            "## Load Order",
            "",
            f"1. `{coordinator_context_pack_path(base)}`",
            f"2. `{coordinator_memory_path(base)}`",
            f"3. `{coordinator_constraints_path(base)}` before launching or redirecting workers",
            f"4. `{schedule_doc_path(base)}` only for audit detail",
            "5. Worker progress/report/jobs/captures only for concrete diagnosis or final review",
            "",
            "## Commands",
            "",
            "```bash",
            f"python {MANAGER_PATH} --state-dir {base} compact-memory --print --context-pack",
            f"python {MANAGER_PATH} --state-dir {base} compact-memory --note '<short current state>' --decision '<decision>' --next-action '<next checkpoint>'",
            f"python {MANAGER_PATH} --state-dir {base} collect --lines 20",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def refresh_compact_memory(base: Path, registry: dict[str, Any] | None = None, *, reason: str = "refresh") -> tuple[Path, Path]:
    registry = registry or load_registry(base)
    memory_path = coordinator_memory_path(base)
    context_pack_path = coordinator_context_pack_path(base)
    write_text(memory_path, render_coordinator_memory(base, registry, reason=reason))
    write_text(context_pack_path, render_coordinator_context_pack(base, registry, reason=reason))
    return memory_path, context_pack_path


def render_coordinator_handoff(base: Path, registry: dict[str, Any], reason: str = "refresh") -> str:
    workers = registry.get("workers", {})
    coordinator = registry.get("coordinator") or {}
    mission = registry.get("mission", "未设置")
    session = registry.get("session", DEFAULT_SESSION)
    rows = []
    for name, worker in sorted(workers.items()):
        rows.append(
            [
                name,
                effective_state(worker, session),
                worker.get("worker_kind", "standard"),
                worker.get("parent_worker") or "main",
                worker.get("mode", "-"),
                f"{worker.get('session', session)}:{worker.get('window', '-')}",
                ", ".join(worker.get("resources", [])) or "-",
                one_line(extract_markdown_section(Path(worker.get("workplan_file", "")), "Task", 160), 160),
            ]
        )

    events = load_schedule_events(base, 30)
    peer_messages = load_peer_messages(base, 20)
    lines = [
        "# Coordinator Recovery Handoff",
        "",
        f"Updated: {now_iso()}",
        f"Reason: {reason}",
        f"State dir: `{base}`",
        f"Mission: {mission}",
        "",
        "## Registered Coordinator",
        "",
    ]
    if coordinator:
        target = coordinator.get("target", "")
        lines.extend(
            [
                f"- Current target: `{target or '-'}`",
                f"- Target state: `{'running' if target and tmux_target_present(target) else 'not-present'}`",
                f"- Working directory: `{coordinator.get('cwd', '-')}`",
                f"- Recovery window prefix: `{coordinator.get('restart_window_prefix', 'cw-main-recovered')}`",
                f"- Model/reasoning: `{coordinator.get('model', '-')}/{coordinator.get('reasoning_effort', '-')}`",
                f"- Registered at: {coordinator.get('registered_at', '-')}",
                f"- Last recovery at: {coordinator.get('last_recovery_at', '-')}",
                f"- Recovery count: {coordinator.get('recovery_count', 0)}",
            ]
        )
    else:
        lines.append("- No coordinator registered yet. Use `register-coordinator` from the tmux-hosted main Codex pane.")

    lines.extend(
        [
            "",
            "## Recovery Protocol For New Main Coordinator",
            "",
            "1. Use the `long-running-autonomous-project-management` and `tmux-codex-parallel-workers` skills.",
            "2. Treat this as a resumed coordinator session, not a new project. Do not restart existing workers from scratch.",
            "3. First inspect the compact durable control plane: context pack, compact memory, schedule, worker registry, progress/report tails, jobs, branch-manager summaries, peer messages, and consultation context.",
            "4. Reconstruct the active mission, active workers, branch-manager hierarchy, resource ownership, blockers, and next checkpoints.",
            "5. Record a `schedule-note` that coordinator recovery happened, then continue normal autonomous supervision and integration.",
            "6. Keep the recovered coordinator context lean: consume `COORDINATOR_CONTEXT_PACK.md` and `COORDINATOR_MEMORY.md` first; load long logs only for concrete diagnosis or final review.",
            "",
            "## First Commands",
            "",
            "```bash",
            f"python {MANAGER_PATH} --state-dir {base} compact-memory --print --context-pack",
            f"python {MANAGER_PATH} --state-dir {base} compact-memory --print",
            f"python {MANAGER_PATH} --state-dir {base} schedule --print",
            f"python {MANAGER_PATH} --state-dir {base} list",
            f"python {MANAGER_PATH} --state-dir {base} jobs",
            f"python {MANAGER_PATH} --state-dir {base} collect --lines 30",
            f"python {MANAGER_PATH} --state-dir {base} consult-context --print",
            "```",
            "",
            "## Worker Overview",
            "",
        ]
    )
    lines.append(markdown_table(["Worker", "状态", "类型", "上级", "模式", "tmux", "资源", "任务摘要"], rows) if rows else "暂无 worker。")
    lines.extend(["", "## Key Files", ""])
    for name, worker in sorted(workers.items()):
        lines.extend(
            [
                f"### {name}",
                f"- workplan: `{worker.get('workplan_file', '-')}`",
                f"- progress: `{worker.get('progress_file', '-')}`",
                f"- report: `{worker.get('report_file', '-')}`",
                f"- jobs: `{worker.get('jobs_file', '-')}`",
                f"- status: `{worker.get('status_file', '-')}`",
                f"- log: `{worker.get('log_file', '-')}`",
                f"- captures: `{base / 'captures' / name}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Coordinator Memory Files",
            "",
            f"- Context pack: `{coordinator_context_pack_path(base)}`",
            f"- Compact memory: `{coordinator_memory_path(base)}`",
            f"- Unified constraints: `{coordinator_constraints_path(base)}`",
            f"- Recovery handoff: `{coordinator_handoff_path(base)}`",
            "",
        ]
    )
    lines.extend(["## Recent Schedule Events", ""])
    if events:
        lines.append(
            markdown_table(
                ["Time", "Event", "Worker", "Detail"],
                [[event.get("timestamp", ""), event.get("event", ""), event.get("worker", ""), one_line(event.get("detail", ""), 140)] for event in events],
            )
        )
    else:
        lines.append("No recent schedule events.")
    lines.extend(["", "## Recent Peer Messages", ""])
    if peer_messages:
        lines.append(
            markdown_table(
                ["Time", "Source", "Target", "Summary", "Inbox"],
                [
                    [
                        item.get("timestamp", ""),
                        item.get("source", ""),
                        item.get("target", ""),
                        one_line(item.get("summary", ""), 120),
                        item.get("inbox_file", ""),
                    ]
                    for item in peer_messages
                ],
            )
        )
    else:
        lines.append("No recent peer messages.")
    lines.extend(
        [
            "",
            "## Coordinator-Owned Decisions Still Required",
            "",
            "- Final merge/promotion/user-facing conclusions remain coordinator-owned.",
            "- Branch-manager summaries should be reviewed before drilling into child worker transcripts.",
            "- Scope/resource changes must be written through `schedule-note`.",
            "- Existing background jobs should be checked before launching duplicates.",
            "",
        ]
    )
    return "\n".join(lines)


def write_coordinator_handoff(base: Path, registry: dict[str, Any], reason: str = "refresh") -> Path:
    path = coordinator_handoff_path(base)
    write_text(path, render_coordinator_handoff(base, registry, reason))
    return path


def ensure_session(session: str, cwd: Path) -> None:
    if tmux("has-session", "-t", session, check=False).returncode == 0:
        return
    tmux("new-session", "-d", "-s", session, "-n", "manager", "-c", str(cwd))


def new_tmux_window(session: str, window: str, cwd: Path, command: str) -> None:
    # Detached window creation keeps attached tmux clients on their current window.
    tmux("new-window", "-d", "-t", session, "-n", window, "-c", str(cwd), "bash", "-lc", command)


def print_window_access(session: str, window: str) -> None:
    print(f"window={session}:{window}")
    print(f"windows=tmux list-windows -t {session}")
    print(f"attach=tmux attach -t {session}  # then select window {window}")
    print(f"switch=tmux switch-client -t {session}:{window}  # from inside tmux")


def window_exists(session: str, window: str) -> bool:
    listed = tmux("list-windows", "-t", session, "-F", "#{window_name}", check=False)
    if listed.returncode != 0:
        return False
    return window in listed.stdout.splitlines()


def tmux_target_present(target: str) -> bool:
    if not target:
        return False
    return tmux("list-panes", "-t", target, "-F", "#{pane_id}", check=False).returncode == 0


def infer_current_tmux_target() -> str | None:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    result = tmux("display-message", "-p", "-t", pane, "#S:#W.#{pane_index}", check=False)
    if result.returncode != 0:
        return None
    target = result.stdout.strip()
    return target or None


def unique_window_name(session: str, prefix: str) -> str:
    base = safe_name(prefix)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = safe_name(f"{base}-{stamp}")
    if not window_exists(session, candidate):
        return candidate
    for index in range(2, 100):
        candidate = safe_name(f"{base}-{stamp}-{index}")
        if not window_exists(session, candidate):
            return candidate
    raise SystemExit(f"Cannot allocate a free tmux window name for prefix: {prefix}")


def send_prompt(
    target: str,
    message: str,
    *,
    escape_first: bool = False,
    escape_after: bool = False,
    enter: bool = True,
) -> None:
    if escape_first:
        tmux("send-keys", "-t", target, "Escape")
        time.sleep(1)
    tmux("set-buffer", message)
    tmux("paste-buffer", "-t", target)
    if enter:
        time.sleep(0.2)
        tmux("send-keys", "-t", target, "C-m")
    if escape_after:
        time.sleep(0.5)
        tmux("send-keys", "-t", target, "Escape")


def capture_target(target: str, lines: int) -> str:
    return tmux("capture-pane", "-p", "-S", f"-{lines}", "-t", target).stdout.rstrip()


def timestamp_slug() -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "-", now_iso()).strip("-")


def read_task(args: argparse.Namespace) -> str:
    if args.task_file:
        return Path(args.task_file).expanduser().read_text(encoding="utf-8").strip()
    if args.task:
        return args.task.strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    raise SystemExit("Provide --task, --task-file, or pipe a task on stdin.")


def write_inbox_message(
    worker: dict[str, Any],
    message: str,
    *,
    title: str = "Coordinator Message",
    source: str | None = None,
    target: str | None = None,
) -> Path:
    inbox = Path(worker.get("inbox_dir") or Path(worker["progress_file"]).parent.parent / "inbox" / worker["name"])
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{timestamp_slug()}.md"
    header = [f"# {title}", "", f"Created: {now_iso()}"]
    if source:
        header.append(f"From: {source}")
    if target:
        header.append(f"To: {target}")
    header.extend(["", message.rstrip(), ""])
    write_text(path, "\n".join(header))
    return path


def normalize_paths(cwd: Path, paths: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw in paths or []:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = cwd / path
        normalized.append(str(path.resolve(strict=False)))
    return normalized


def find_git_root(cwd: Path) -> Path:
    require_binary("git")
    result = git(["rev-parse", "--show-toplevel"], cwd=cwd, check=False)
    if result.returncode != 0:
        raise SystemExit(f"Not inside a git repository: {cwd}")
    return Path(result.stdout.strip()).resolve()


def git_ref_exists(cwd: Path, ref: str) -> bool:
    return git(["show-ref", "--verify", "--quiet", f"refs/heads/{ref}"], cwd=cwd, check=False).returncode == 0


def unique_branch_name(cwd: Path, base_name: str) -> str:
    branch = f"codex-worker/{safe_name(base_name)}"
    if not git_ref_exists(cwd, branch):
        return branch
    suffix = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = f"{branch}-{suffix}"
    if git_ref_exists(cwd, candidate):
        raise SystemExit(f"Cannot allocate a unique git branch for worker: {base_name}")
    return candidate


def create_git_worktree(base: Path, name: str, cwd: Path, args: argparse.Namespace) -> tuple[Path, dict[str, str]]:
    root = find_git_root(cwd)
    branch = args.branch or unique_branch_name(root, name)
    base_ref = args.base_ref or "HEAD"
    worktree = Path(args.worktree_path).expanduser().resolve() if args.worktree_path else (base / "git-worktrees" / name).resolve()
    if worktree.exists() and any(worktree.iterdir()):
        raise SystemExit(f"Git worktree path already exists and is non-empty: {worktree}")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = git(["worktree", "add", "-b", branch, str(worktree), base_ref], cwd=root, check=False)
    if result.returncode != 0:
        raise SystemExit(f"Failed to create git worktree:\n{result.stderr.strip()}")
    return worktree, {"git_root": str(root), "worktree_path": str(worktree), "branch": branch, "base_ref": base_ref}


def git_summary(cwd: Path) -> str:
    if git(["rev-parse", "--is-inside-work-tree"], cwd=cwd, check=False).returncode != 0:
        return "Not a git worktree."
    status = git(["status", "--short"], cwd=cwd, check=False).stdout.strip()
    stat = git(["diff", "--stat"], cwd=cwd, check=False).stdout.strip()
    staged = git(["diff", "--cached", "--stat"], cwd=cwd, check=False).stdout.strip()
    parts = []
    parts.append("### Git Status\n")
    parts.append(status or "clean")
    parts.append("\n### Unstaged Diff Stat\n")
    parts.append(stat or "none")
    parts.append("\n### Staged Diff Stat\n")
    parts.append(staged or "none")
    return "\n".join(parts)


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    def cell(value: str) -> str:
        return str(value).replace("\n", "<br>").replace("|", "\\|")

    lines = ["| " + " | ".join(cell(item) for item in headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(cell(item) for item in row) + " |")
    return "\n".join(lines)


def render_schedule_doc(base: Path, registry: dict[str, Any]) -> str:
    workers = registry.get("workers", {})
    session = registry.get("session", DEFAULT_SESSION)
    mission = registry.get("mission", "未设置。使用 schedule-note 或 init --mission 记录当前总目标。")
    consult = registry.get("consult") or {}
    coordinator = registry.get("coordinator") or {}
    lines: list[str] = [
        "# Codex Tmux Workers 调度总览",
        "",
        f"更新时间：{now_iso()}",
        f"State dir：`{base}`",
        f"tmux session：`{session}`",
        f"默认 worker 模型：`{registry.get('default_worker_model', DEFAULT_WORKER_MODEL)}`",
        f"默认 reasoning effort：`{registry.get('default_worker_reasoning', DEFAULT_WORKER_REASONING)}`",
        "上下文预算：调度文档只保留摘要和证据路径；完整日志、长输出、完整 diff 和 tmux transcript 保存在对应 artifact/log/capture 文件中。",
        f"当前总目标：{mission}",
        "",
        "## 主进程恢复与接管",
        "",
    ]
    if coordinator:
        coord_target = coordinator.get("target", "")
        coord_state = "running" if coord_target and tmux_target_present(coord_target) else "not-present"
        lines.extend(
            [
                "- 注册状态：`registered`",
                f"- 当前主进程 target：`{coord_target or '-'}`",
                f"- target 状态：`{coord_state}`",
                f"- 工作目录：`{coordinator.get('cwd', '-')}`",
                f"- 恢复窗口前缀：`{coordinator.get('restart_window_prefix', 'cw-main-recovered')}`",
                f"- model：`{coordinator.get('model', '-')}`",
                f"- reasoning effort：`{coordinator.get('reasoning_effort', '-')}`",
                f"- 接管 handoff：`{coordinator.get('handoff_file', coordinator_handoff_path(base))}`",
                f"- recovery count：{coordinator.get('recovery_count', 0)}",
                f"- last recovery：{coordinator.get('last_recovery_at', '-')}",
                f"- 启用自动接管的 health supervisor：`python {MANAGER_PATH} --state-dir {base} --session {session} start-health-supervisor --restart-main-on-context-full --restart-main-when-missing`",
            ]
        )
    else:
        lines.extend(
            [
                "- 未注册主进程。若主 Codex 本身运行在 tmux 中，建议从主进程窗口执行：",
                "",
                "```bash",
                "tmux display-message -p '#S:#W.#{pane_index}'",
                f"python {MANAGER_PATH} --state-dir {base} --session {session} register-coordinator --target <SESSION:WINDOW.PANE> --cwd \"$PWD\"",
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "## 主进程压缩记忆",
            "",
            f"- 短上下文包：`{coordinator_context_pack_path(base)}`",
            f"- 主进程压缩记忆：`{coordinator_memory_path(base)}`",
            f"- 统一运行约束：`{coordinator_constraints_path(base)}`",
            f"- 约束事件：`{coordinator_constraints_events_path(base)}`",
            f"- 压缩记忆事件：`{coordinator_memory_events_path(base)}`",
            f"- 刷新并查看短包：`python {MANAGER_PATH} --state-dir {base} compact-memory --print --context-pack`",
            f"- 写入关键决策：`python {MANAGER_PATH} --state-dir {base} compact-memory --note '<short state>' --decision '<decision>' --next-action '<next checkpoint>'`",
            f"- 查看/更新统一约束：`python {MANAGER_PATH} --state-dir {base} constraints --print`",
            "- 主进程应优先读取短上下文包和压缩记忆；只有审查、诊断或最终收口时才读取完整 schedule、collect、capture、log 或 report。",
            "",
            "## 用户咨询窗口",
            "",
        ]
    )
    if consult:
        consult_session = consult.get("session", session)
        consult_window = consult.get("window", "")
        consult_state = "stopped" if consult.get("stopped_at") else ("running" if window_exists(consult_session, consult_window) else "not-present")
        lines.extend(
            [
                f"- 状态：`{consult_state}`",
                f"- tmux：`{consult_session}:{consult_window or '-'}`",
                f"- model：`{consult.get('model', '-')}`",
                f"- reasoning effort：`{consult.get('reasoning_effort', '-')}`",
                f"- 咨询上下文：`{consult.get('context_file', consult_context_path(base))}`",
                f"- 日志：`{consult.get('log_file', consult_log_path(base))}`",
                f"- 连接命令：`tmux attach -t {consult_session}`，然后切换到 `{consult_window or 'cw-consult'}` 窗口",
            ]
        )
        if consult.get("stopped_at"):
            lines.append(f"- 停止时间：{consult.get('stopped_at')}")
    else:
        lines.append(f"- 未启动。可用 `python {MANAGER_PATH} --state-dir {base} start-consult` 启动只读用户咨询窗口。")
    lines.extend(
        [
            "",
            "## 用户审查入口",
            "",
            f"- 查看 worker：`python {MANAGER_PATH} --state-dir {base} list`",
            f"- 查看进展：`python {MANAGER_PATH} --state-dir {base} progress <worker> --lines 40`",
            f"- 查看 jobs：`python {MANAGER_PATH} --state-dir {base} jobs`",
            f"- 查看 worker 横向消息：`{peer_messages_path(base)}`",
            f"- 查看咨询上下文：`python {MANAGER_PATH} --state-dir {base} consult-context --print`",
            f"- 查看短上下文包：`python {MANAGER_PATH} --state-dir {base} compact-memory --print --context-pack`",
            f"- 查看压缩记忆：`python {MANAGER_PATH} --state-dir {base} compact-memory --print`",
            f"- 查看统一约束：`python {MANAGER_PATH} --state-dir {base} constraints --print`",
            f"- 查看主进程接管 handoff：`{coordinator_handoff_path(base)}`",
            f"- 汇总收口：`python {MANAGER_PATH} --state-dir {base} collect --lines 30`",
            f"- 连接 tmux：`tmux attach -t {session}`",
            "",
        ]
    )

    rows = []
    for name, worker in sorted(workers.items()):
        status = effective_state(worker, session)
        task = extract_markdown_section(Path(worker.get("workplan_file", "")), "Task", 180)
        git_meta = worker.get("git_worktree") or {}
        branch = git_meta.get("branch", "-") if isinstance(git_meta, dict) else "-"
        rows.append(
            [
                name,
                status,
                worker.get("worker_kind", "standard"),
                worker.get("parent_worker") or "main",
                worker.get("mode", "-"),
                f"{worker.get('session', session)}:{worker.get('window', '-')}",
                ", ".join(worker.get("resources", [])) or "-",
                f"{worker.get('model', '-')}/{worker.get('reasoning_effort', '-')}",
                branch,
                one_line(task, 120),
            ]
        )
    lines.extend(["## Worker 总表", ""])
    lines.append(markdown_table(["Worker", "状态", "类型", "上级", "模式", "tmux", "资源", "模型/推理", "Git branch", "任务摘要"], rows) if rows else "暂无 worker。")
    lines.append("")

    lines.extend(["## Worker 明细", ""])
    for name, worker in sorted(workers.items()):
        status_data = read_status_file(worker) or {}
        jobs = load_jobs(jobs_path_for(base, worker), name).get("jobs", [])
        progress_file = Path(worker.get("progress_file", ""))
        report_file = Path(worker.get("report_file", ""))
        workplan_file = Path(worker.get("workplan_file", ""))
        cwd = Path(worker.get("cwd", "."))
        git_meta = worker.get("git_worktree") or {}
        lines.extend(
            [
                f"### {name}",
                "",
                f"- 状态：`{effective_state(worker, session)}`",
                f"- 类型：`{worker.get('worker_kind', 'standard')}`",
                f"- 上级 worker：`{worker.get('parent_worker') or 'main-coordinator'}`",
                f"- 启动时间：{worker.get('created_at', '-')}",
                f"- 更新时间：{worker.get('updated_at', '-')}",
                f"- tmux：`{worker.get('session', session)}:{worker.get('window', '-')}`",
                f"- model：`{worker.get('model', '-')}`",
                f"- reasoning effort：`{worker.get('reasoning_effort', '-')}`",
                f"- 工作目录：`{worker.get('cwd', '-')}`",
                f"- owned paths：{', '.join(worker.get('owned_paths', [])) or '-'}",
                f"- resources：{', '.join(worker.get('resources', [])) or '-'}",
                f"- manager scope：{', '.join(worker.get('manager_scope', [])) or '-'}",
                f"- workplan：`{workplan_file}`",
                f"- progress：`{progress_file}`",
                f"- report：`{report_file}`",
                f"- inbox：`{worker.get('inbox_dir', '-')}`",
                f"- jobs：`{worker.get('jobs_file', '-')}`",
            ]
        )
        if isinstance(git_meta, dict) and git_meta:
            lines.extend(
                [
                    f"- git root：`{git_meta.get('git_root', '-')}`",
                    f"- git worktree：`{git_meta.get('worktree_path', '-')}`",
                    f"- git branch：`{git_meta.get('branch', '-')}`",
                    f"- base ref：`{git_meta.get('base_ref', '-')}`",
                ]
            )
        if status_data:
            lines.append(f"- status json：`{worker.get('status_file', '-')}`")
            if status_data.get("stalled_seconds") is not None:
                lines.append(f"- stalled seconds：{status_data.get('stalled_seconds')}")
        lines.extend(["", "#### 任务", "", extract_markdown_section(workplan_file, "Task", 800), ""])
        lines.extend(["#### 调度和资源", ""])
        lines.append(extract_markdown_section(workplan_file, "Resources", 500))
        lines.append("")
        lines.append(extract_markdown_section(workplan_file, "Owned Paths", 500))
        lines.append("")
        lines.extend(["#### 后台 Jobs", ""])
        lines.append("\n".join(job_line(job) for job in jobs) if jobs else "无已登记后台 job。")
        lines.extend(["", "#### 最新进展摘录", "", "```text", tail_text(progress_file, 18), "```", ""])
        lines.extend(["#### 最新报告摘录", "", "```text", tail_text(report_file, 18), "```", ""])
        if cwd.exists():
            lines.extend(["#### Git 摘要", "", "```text", git_summary(cwd), "```", ""])

    events = load_schedule_events(base, 40)
    lines.extend(["## 调度事件日志", ""])
    if events:
        event_rows = [
            [
                event.get("timestamp", ""),
                event.get("event", ""),
                event.get("worker", ""),
                one_line(event.get("detail", ""), 140),
            ]
            for event in events
        ]
        lines.append(markdown_table(["时间", "事件", "Worker", "说明"], event_rows))
    else:
        lines.append("暂无调度事件。")

    peer_messages = load_peer_messages(base, 30)
    lines.extend(["", "## Worker 横向消息", ""])
    if peer_messages:
        peer_rows = [
            [
                item.get("timestamp", ""),
                item.get("source", ""),
                item.get("target", ""),
                one_line(item.get("summary", ""), 120),
                item.get("inbox_file", ""),
            ]
            for item in peer_messages
        ]
        lines.append(markdown_table(["时间", "来源", "目标", "摘要", "Inbox"], peer_rows))
    else:
        lines.append("暂无 worker 横向消息。")
    lines.extend(
        [
            "",
            "## 主进程审查清单",
            "",
            "- 是否所有 worker 都有明确任务、owned paths 和资源声明。",
            "- 是否存在 stopped/failed/stalled worker 需要恢复或终止。",
            "- 后台 jobs 是否仍在运行，PID/log/resource 是否清楚。",
            "- worker report 中的结果是否有日志、指标、测试或文件路径证据。",
            "- git worktree 的 diff 是否已由主进程审查，是否需要合并。",
            "- 最终对用户汇报前，主进程是否运行了必要的测试或评估。",
            "",
        ]
    )
    return "\n".join(lines)


def render_consult_context(base: Path, registry: dict[str, Any]) -> str:
    workers = registry.get("workers", {})
    session = registry.get("session", DEFAULT_SESSION)
    mission = registry.get("mission", "未设置")
    coordinator = registry.get("coordinator") or {}
    rows = []
    for name, worker in sorted(workers.items()):
        rows.append(
            [
                name,
                effective_state(worker, session),
                worker.get("worker_kind", "standard"),
                worker.get("parent_worker") or "main",
                worker.get("mode", "-"),
                f"{worker.get('session', session)}:{worker.get('window', '-')}",
                f"{worker.get('model', '-')}/{worker.get('reasoning_effort', '-')}",
                ", ".join(worker.get("resources", [])) or "-",
                one_line(extract_markdown_section(Path(worker.get("workplan_file", "")), "Task", 140), 140),
            ]
        )

    lines = [
        "# 用户咨询窗口上下文",
        "",
        f"更新时间：{now_iso()}",
        f"当前总目标：{mission}",
        f"State dir：`{base}`",
        f"调度总览：`{schedule_doc_path(base)}`",
        f"调度事件：`{schedule_events_path(base)}`",
        f"worker 横向消息：`{peer_messages_path(base)}`",
        f"主进程接管 handoff：`{coordinator_handoff_path(base)}`",
        f"主进程短上下文包：`{coordinator_context_pack_path(base)}`",
        f"主进程压缩记忆：`{coordinator_memory_path(base)}`",
        f"统一运行约束：`{coordinator_constraints_path(base)}`",
        f"注册主进程 target：`{coordinator.get('target', '-') if coordinator else '-'}`",
        "",
        "## 咨询 worker 规则",
        "",
        "- 你是只读的用户咨询窗口 worker，负责回答用户关于当前长程自主任务、worker、日志、结果、资源和下一步的问题。",
        "- 每次回答用户问题前，先读取本文件和调度总览文件；必要时再读取 progress/report/jobs/captures/logs。",
        "- 默认用中文回答，给出可审查的文件路径和证据，不要凭记忆回答。",
        "- 默认简洁回答：先给结论、状态、证据路径和下一步；长日志、长表格、完整 diff 或完整 transcript 只给路径和短摘要。",
        "- 不要启动、停止、恢复 worker，不要修改项目文件，不要改调度状态；如果用户要求执行操作，说明应由主进程或 manager 命令执行。",
        "- 如果信息缺失，明确说明缺失的文件或尚未完成的 worker，而不是猜测。",
        "",
        "## 快速审查命令",
        "",
        f"- 咨询上下文：`python {MANAGER_PATH} --state-dir {base} consult-context --print`",
        f"- 调度总览：`python {MANAGER_PATH} --state-dir {base} schedule --print`",
        f"- 短上下文包：`python {MANAGER_PATH} --state-dir {base} compact-memory --print --context-pack`",
        f"- 统一运行约束：`python {MANAGER_PATH} --state-dir {base} constraints --print`",
        f"- worker 列表：`python {MANAGER_PATH} --state-dir {base} list`",
        f"- jobs：`python {MANAGER_PATH} --state-dir {base} jobs`",
        f"- collect：`python {MANAGER_PATH} --state-dir {base} collect --lines 30`",
        "",
        "## Worker 总览",
        "",
    ]
    lines.append(markdown_table(["Worker", "状态", "类型", "上级", "模式", "tmux", "模型/推理", "资源", "任务摘要"], rows) if rows else "暂无 worker。")
    lines.extend(["", "## 关键文件", ""])
    for name, worker in sorted(workers.items()):
        lines.extend(
            [
                f"### {name}",
                f"- workplan：`{worker.get('workplan_file', '-')}`",
                f"- progress：`{worker.get('progress_file', '-')}`",
                f"- report：`{worker.get('report_file', '-')}`",
                f"- jobs：`{worker.get('jobs_file', '-')}`",
                f"- status：`{worker.get('status_file', '-')}`",
                f"- log：`{worker.get('log_file', '-')}`",
                f"- captures：`{base / 'captures' / name}`",
                "",
            ]
        )
    lines.extend(["## 最近调度事件", ""])
    events = load_schedule_events(base, 30)
    if events:
        lines.append(
            markdown_table(
                ["时间", "事件", "Worker", "说明"],
                [[event.get("timestamp", ""), event.get("event", ""), event.get("worker", ""), one_line(event.get("detail", ""), 120)] for event in events],
            )
        )
    else:
        lines.append("暂无调度事件。")
    lines.extend(
        [
            "",
            "## 调度总览摘录",
            "",
            "```text",
            tail_text(schedule_doc_path(base), 100),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def refresh_consult_context(base: Path, registry: dict[str, Any] | None = None) -> Path:
    registry = registry or load_registry(base)
    path = consult_context_path(base)
    write_text(path, render_consult_context(base, registry))
    return path


def refresh_schedule_doc(base: Path, registry: dict[str, Any] | None = None) -> Path:
    registry = registry or load_registry(base)
    ensure_constraints_doc(base)
    path = schedule_doc_path(base)
    write_text(path, render_schedule_doc(base, registry))
    refresh_compact_memory(base, registry)
    refresh_consult_context(base, registry)
    write_coordinator_handoff(base, registry)
    return path


def paths_overlap(a: str, b: str) -> bool:
    left = Path(a)
    right = Path(b)
    return left == right or left in right.parents or right in left.parents


def read_status_file(worker: dict[str, Any]) -> dict[str, Any] | None:
    raw = worker.get("status_file")
    if not raw:
        return None
    path = Path(raw)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"state": "unknown", "status_error": f"invalid json in {path}"}


def effective_state(worker: dict[str, Any], default_session: str) -> str:
    if worker.get("stopped_at"):
        return "stopped"
    status = read_status_file(worker) or {}
    state = status.get("state")
    if state in {"completed", "failed"}:
        return str(state)
    session = worker.get("session", default_session)
    window = worker.get("window", "")
    if window and window_exists(session, window):
        return "running"
    if state == "launched":
        return "not-present"
    return str(state or "not-present")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def jobs_path_for(base: Path, worker: dict[str, Any] | str) -> Path:
    if isinstance(worker, dict):
        raw = worker.get("jobs_file")
        if raw:
            return Path(raw)
        name = worker["name"]
    else:
        name = worker
    return base / "jobs" / f"{name}.json"


def load_jobs(path: Path, worker_name: str) -> dict[str, Any]:
    data = read_json(path, {"worker": worker_name, "jobs": []})
    data.setdefault("worker", worker_name)
    data.setdefault("jobs", [])
    return data


def save_jobs(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def job_line(job: dict[str, Any]) -> str:
    pid = int(job.get("pid", 0))
    alive = pid_alive(pid) if pid else False
    state = "alive" if alive else "dead"
    log = job.get("log", "-")
    resources = ",".join(job.get("resources", [])) or "-"
    return f"{job.get('worker','-')}\t{job.get('name','-')}\tpid={pid}\t{state}\tresources={resources}\tlog={log}"


def check_launch_conflicts(
    registry: dict[str, Any],
    *,
    name: str,
    session: str,
    owned_paths: list[str],
    resources: list[str],
    allow_conflict: bool,
) -> None:
    if allow_conflict:
        return
    conflicts: list[str] = []
    for other_name, worker in registry.get("workers", {}).items():
        if other_name == name:
            continue
        if effective_state(worker, session) not in {"running", "launched"}:
            continue
        for resource in resources:
            if resource and resource in worker.get("resources", []):
                conflicts.append(f"resource {resource!r} is already owned by {other_name}")
        for path in owned_paths:
            for other_path in worker.get("owned_paths", []):
                if paths_overlap(path, other_path):
                    conflicts.append(f"path {path!r} overlaps {other_name}:{other_path!r}")
    if conflicts:
        joined = "\n- ".join(conflicts)
        raise SystemExit(f"Launch conflict detected. Use --allow-conflict only if intentional.\n- {joined}")


def create_worker_docs(
    *,
    base: Path,
    name: str,
    session: str,
    window: str,
    worker_kind: str,
    cwd: Path,
    task: str,
    write_scope: list[str],
    owned_paths: list[str],
    resources: list[str],
    git_meta: dict[str, str] | None,
    model: str | None,
    reasoning_effort: str | None,
    notes: str | None,
    parent_worker: str | None,
    manager_scope: list[str],
) -> dict[str, Path]:
    started = now_iso()
    workplan = base / "workplans" / f"{name}.md"
    progress = base / "progress" / f"{name}.md"
    report = base / "reports" / f"{name}.md"
    inbox = base / "inbox" / name
    status = base / "status" / f"{name}.json"
    jobs = base / "jobs" / f"{name}.json"
    constraints = ensure_constraints_doc(base)

    scope = "\n".join(f"- {item}" for item in write_scope) if write_scope else "- Read-only unless explicitly required by the task."
    owned = "\n".join(f"- {item}" for item in owned_paths) if owned_paths else "- None declared."
    resource_text = "\n".join(f"- {item}" for item in resources) if resources else "- None declared."
    git_text = "\n".join(f"- {key}: {value}" for key, value in (git_meta or {}).items()) if git_meta else "- Shared working tree."
    note_text = notes or "None."
    parent_text = parent_worker or "main-coordinator"
    manager_scope_text = "\n".join(f"- {item}" for item in manager_scope) if manager_scope else "- None declared."
    branch_manager_text = (
        f"""You may coordinate subordinate front-line workers for this branch through manager commands.

Launch child workers with explicit ownership and this parent marker:

```bash
python {MANAGER_PATH} --state-dir {base} --session {session} launch <child-worker> --parent-worker {name} --worker-kind autonomous-experiment --task '<bounded child task>'
```

Use `peer-send` for short worker-to-worker evidence transfer, and use `schedule-note` for non-obvious branch decisions. Do not edit registry, status, schedule, or consultation files directly.
"""
        if worker_kind == "branch-manager"
        else "Not a branch-manager worker."
    )
    inbox.mkdir(parents=True, exist_ok=True)
    write_text(
        workplan,
        f"""# Codex Tmux Worker Plan - {name}

Created: {started}
Worker kind: {worker_kind}
Parent worker: {parent_text}
Session: {session}
Window: {window}
Working directory: {cwd}
Model: {model or "Codex CLI default"}
Reasoning effort: {reasoning_effort or "Codex CLI default"}

## Task

{task}

## Write Scope

{scope}

## Owned Paths

{owned}

## Resources

{resource_text}

## Manager Scope

{manager_scope_text}

## Unified Coordinator Constraints

Read and obey this file before starting work, launching child workers, opening TensorBoard, binding ports, starting background jobs, or changing resources:

{constraints}

## Git Isolation

{git_text}

## Inbox

{inbox}

## Background Job Registry

{jobs}

Register background jobs with:

```bash
python {MANAGER_PATH} --state-dir {base} --session {session} job-add {name} --pid <PID> --name <job-name> --log <log-path> --command '<command>'
```

## Peer Communication

Send short evidence or dependency messages to another worker with:

```bash
python {MANAGER_PATH} --state-dir {base} --session {session} peer-send {name} <target-worker> --message '<short factual message with evidence paths>'
```

Peer messages may share evidence, blockers, and coordination facts. They must not unilaterally change another worker's assigned scope, resources, or final decision gate.

## Branch Manager Instructions

{branch_manager_text}

## Coordinator Notes

{note_text}

## Required Completion Report

- Changed files, if any
- Commands run
- Result or metric evidence
- Blockers
- Next recommended action
""",
    )
    write_text(
        progress,
        f"""# {name} Progress

Updated: {started}
Status: launched
Worker kind: {worker_kind}
Parent worker: {parent_text}
Session: {session}:{window}
Working directory: {cwd}

## Current Progress

- Started with task plan: {workplan}
- Inbox directory: {inbox}
- Background job registry: {jobs}
- Log file will be updated under this worker state directory.

## Next

- Worker should update this file when key milestones, blockers, or completion occur.
""",
    )
    write_text(
        report,
        f"""# Codex Tmux Worker Report - {name}

Created: {started}
Worker kind: {worker_kind}
Parent worker: {parent_text}
Task: {task}
Session: {session}:{window}

## Summary

Pending worker updates.

## Evidence

Pending.

## Completion

Pending.
""",
    )
    write_text(status, json.dumps({"state": "launched", "started_at": started}, ensure_ascii=False) + "\n")
    write_text(jobs, json.dumps({"worker": name, "jobs": []}, ensure_ascii=False, indent=2) + "\n")
    return {"workplan": workplan, "progress": progress, "report": report, "inbox": inbox, "status": status, "jobs": jobs}


def codex_command(
    *,
    mode: str,
    cwd: Path,
    prompt_file: Path,
    log_file: Path,
    status_file: Path,
    add_dirs: list[Path] | None,
    model: str | None,
    reasoning_effort: str | None,
    profile: str | None,
    sandbox: str,
    approval: str,
    search: bool,
    inline_tui: bool = False,
) -> str:
    base = ["codex"]
    if model:
        base.extend(["--model", model])
    if reasoning_effort:
        base.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    if profile:
        base.extend(["--profile", profile])
    base.extend(["--cd", str(cwd), "--sandbox", sandbox, "--ask-for-approval", approval])
    if sandbox == "workspace-write":
        seen_add_dirs = {str(cwd.resolve(strict=False))}
        for add_dir in add_dirs or []:
            resolved = add_dir.expanduser().resolve(strict=False)
            if str(resolved) not in seen_add_dirs:
                base.extend(["--add-dir", str(resolved)])
                seen_add_dirs.add(str(resolved))
    if search:
        base.append("--search")
    if mode == "exec":
        base.append("exec")
    if mode == "interactive" and inline_tui:
        base.append("--no-alt-screen")

    # The local shell wraps `codex` with --dangerously-bypass-approvals-and-sandbox.
    # Disable that wrapper here so manager-declared sandbox/approval/search flags
    # are passed directly to the Codex CLI without mutually exclusive options.
    quoted_base = "CODEX_DISABLE_DANGER_FULL_ACCESS=1 " + " ".join(shlex.quote(part) for part in base)
    quoted_log = shlex.quote(str(log_file))
    quoted_status = shlex.quote(str(status_file))
    status_write = (
        f"state=failed; [ \"$status\" -eq 0 ] && state=completed; "
        f"printf '{{\"state\":\"%s\",\"exited_at\":\"%s\",\"exit_code\":%s}}\\n' "
        f"\"$state\" \"$(date -Is)\" \"$status\" > {quoted_status}; "
    )
    if mode == "interactive":
        return (
            "set -uo pipefail; "
            f"printf '[%s] interactive worker started\\n' \"$(date -Is)\" | tee -a {quoted_log}; "
            f"{quoted_base}; "
            "status=$?; "
            f"printf '[%s] interactive worker exited status=%s\\n' \"$(date -Is)\" \"$status\" | tee -a {quoted_log}; "
            f"{status_write}"
            "exec bash"
        )
    return (
        "set -uo pipefail; "
        f"printf '[%s] worker started\\n' \"$(date -Is)\" | tee -a {quoted_log}; "
        f"{quoted_base} \"$(cat {shlex.quote(str(prompt_file))})\" "
        f"2>&1 | tee -a {quoted_log}; "
        "status=${PIPESTATUS[0]}; "
        f"printf '[%s] worker exited status=%s\\n' \"$(date -Is)\" \"$status\" | tee -a {quoted_log}; "
        f"{status_write}"
        "exec bash"
    )


def supervisor_command(
    base: Path,
    session: str,
    interval: int,
    ask: bool,
    lines: int,
    query_interval: int,
    refresh_schedule_interval: int,
    progress_append_interval: int,
) -> str:
    cmd = [
        sys.executable,
        str(MANAGER_PATH),
        "--state-dir",
        str(base),
        "--session",
        session,
        "supervise",
        "--interval",
        str(interval),
        "--lines",
        str(lines),
        "--query-interval",
        str(query_interval),
        "--refresh-schedule-interval",
        str(refresh_schedule_interval),
        "--progress-append-interval",
        str(progress_append_interval),
        "--allow-foreground-loop",
    ]
    if ask:
        cmd.append("--query-interactive")
    quoted = " ".join(shlex.quote(part) for part in cmd)
    log_file = base / "logs" / "supervisor.log"
    return (
        "set -uo pipefail; "
        f"mkdir -p {shlex.quote(str(log_file.parent))}; "
        f"printf '[%s] supervisor started\\n' \"$(date -Is)\" | tee -a {shlex.quote(str(log_file))}; "
        f"{quoted} 2>&1 | tee -a {shlex.quote(str(log_file))}; "
        "exec bash"
    )


def health_supervisor_command(
    base: Path,
    session: str,
    interval: int,
    lines: int,
    stable_seconds: int,
    cooldown: int,
    watch_targets: list[str],
    observe_targets: list[str],
    no_workers: bool,
    no_coordinator: bool,
    restart_main_on_context_full: bool,
    restart_main_when_missing: bool,
    keep_old_main: bool,
    dry_run: bool,
    escape_after: bool,
    recovery_prompt: str | None,
) -> str:
    cmd = [
        sys.executable,
        str(HEALTH_SUPERVISOR_PATH),
        "--state-dir",
        str(base),
        "--session",
        session,
        "--interval",
        str(interval),
        "--lines",
        str(lines),
        "--stable-seconds",
        str(stable_seconds),
        "--cooldown",
        str(cooldown),
    ]
    if no_workers:
        cmd.append("--no-workers")
    if no_coordinator:
        cmd.append("--no-coordinator")
    if restart_main_on_context_full:
        cmd.append("--restart-main-on-context-full")
    if restart_main_when_missing:
        cmd.append("--restart-main-when-missing")
    if keep_old_main:
        cmd.append("--keep-old-main")
    for target in watch_targets:
        cmd.extend(["--watch-target", target])
    for target in observe_targets:
        cmd.extend(["--observe-target", target])
    if dry_run:
        cmd.append("--dry-run")
    if escape_after:
        cmd.append("--escape-after")
    if recovery_prompt:
        cmd.extend(["--recovery-prompt", recovery_prompt])
    quoted = " ".join(shlex.quote(part) for part in cmd)
    log_file = base / "logs" / "health-supervisor.log"
    return (
        "set -uo pipefail; "
        "export CODEX_HEALTH_SUPERVISOR_MANAGED=1; "
        f"mkdir -p {shlex.quote(str(log_file.parent))}; "
        f"printf '[%s] health supervisor started\\n' \"$(date -Is)\" | tee -a {shlex.quote(str(log_file))}; "
        f"{quoted} 2>&1; "
        "exec bash"
    )


def cmd_init(args: argparse.Namespace) -> None:
    require_binary("tmux")
    require_binary("codex")
    base = state_dir(args.state_dir)
    cwd = Path(args.cwd).expanduser().resolve()
    ensure_session(args.session, cwd)
    constraints_file = ensure_constraints_doc(base)
    registry = load_registry(base)
    registry["session"] = args.session
    registry["state_dir"] = str(base)
    registry["default_worker_model"] = DEFAULT_WORKER_MODEL
    registry["default_worker_reasoning"] = DEFAULT_WORKER_REASONING
    if args.mission:
        registry["mission"] = args.mission
    registry.setdefault("workers", {})
    registry["updated_at"] = now_iso()
    save_registry(base, registry)
    append_manager_log(base, f"init session={args.session} cwd={cwd}")
    append_schedule_event(base, "init", detail=f"Initialized session {args.session} at {cwd}")
    schedule_path = refresh_schedule_doc(base, registry)
    print(f"session={args.session}")
    print(f"state_dir={base}")
    print(f"constraints={constraints_file}")
    print(f"schedule={schedule_path}")


def cmd_register_coordinator(args: argparse.Namespace) -> None:
    require_binary("tmux")
    base = state_dir(args.state_dir)
    constraints_file = ensure_constraints_doc(base)
    cwd = Path(args.cwd).expanduser().resolve()
    target = args.target
    if not target or target == "auto":
        target = infer_current_tmux_target()
    if not target:
        raise SystemExit("No coordinator tmux target provided and current process is not inside tmux. Pass --target SESSION:WINDOW.PANE.")
    if not args.allow_missing and not tmux_target_present(target):
        raise SystemExit(f"coordinator target is not present: {target}; pass --allow-missing only for tests or pre-registration")
    model, reasoning_effort = resolve_model_settings(args)
    registry = load_registry(base)
    registry["session"] = args.session
    registry["state_dir"] = str(base)
    registry["default_worker_model"] = DEFAULT_WORKER_MODEL
    registry["default_worker_reasoning"] = DEFAULT_WORKER_REASONING
    if args.mission:
        registry["mission"] = args.mission
    previous = registry.get("coordinator") or {}
    coordinator = {
        "role": "main-coordinator",
        "target": target,
        "session": args.session,
        "cwd": str(cwd),
        "restart_window_prefix": safe_name(args.restart_window_prefix),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "best_model_default": not args.no_best_model,
        "profile": args.profile,
        "sandbox": args.sandbox,
        "approval": args.approval,
        "search": bool(args.search),
        "handoff_file": str(coordinator_handoff_path(base)),
        "status_file": str(coordinator_status_path(base)),
        "log_file": str(coordinator_log_path(base)),
        "registered_at": previous.get("registered_at", now_iso()),
        "updated_at": now_iso(),
        "recovery_count": int(previous.get("recovery_count", 0)),
        "last_recovery_at": previous.get("last_recovery_at"),
        "previous_targets": previous.get("previous_targets", []),
    }
    registry["coordinator"] = coordinator
    registry["updated_at"] = now_iso()
    save_registry(base, registry)
    write_text(coordinator_status_path(base), json.dumps({"state": "registered", "target": target, "updated_at": now_iso()}, ensure_ascii=False) + "\n")
    append_manager_log(base, f"register-coordinator target={target} cwd={cwd}")
    append_schedule_event(base, "register-coordinator", detail=f"Registered main coordinator target={target} cwd={cwd}")
    handoff = write_coordinator_handoff(base, registry, "register-coordinator")
    schedule_path = refresh_schedule_doc(base, registry)
    print(f"coordinator={target}")
    print(f"constraints={constraints_file}")
    print(f"handoff={handoff}")
    print(f"schedule={schedule_path}")


def cmd_recover_coordinator(args: argparse.Namespace) -> None:
    require_binary("tmux")
    if not args.dry_run:
        require_binary("codex")
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    constraints_file = ensure_constraints_doc(base)
    coordinator = registry.get("coordinator") or {}
    if not coordinator and not args.cwd:
        raise SystemExit("No registered coordinator found. Run register-coordinator first or pass --cwd for manual recovery.")
    cwd = Path(args.cwd or coordinator.get("cwd", os.getcwd())).expanduser().resolve()
    session = args.session or coordinator.get("session", DEFAULT_SESSION)
    ensure_session(session, cwd)
    old_target = args.old_target or coordinator.get("target", "")
    reason = args.reason or "manual-recovery"
    window = safe_name(args.window) if args.window else unique_window_name(session, coordinator.get("restart_window_prefix", "cw-main-recovered"))
    if window_exists(session, window):
        if not args.force:
            raise SystemExit(f"tmux window already exists: {session}:{window}; use --force to replace it")
        tmux("kill-window", "-t", f"{session}:{window}", check=False)
        time.sleep(0.5)

    model = args.model if args.model is not None else coordinator.get("model")
    reasoning_effort = args.reasoning_effort if args.reasoning_effort is not None else coordinator.get("reasoning_effort")
    if not args.no_best_model and not model:
        model = DEFAULT_WORKER_MODEL
    if not args.no_best_model and not reasoning_effort:
        reasoning_effort = DEFAULT_WORKER_REASONING
    profile = args.profile if args.profile is not None else coordinator.get("profile")
    sandbox = args.sandbox or coordinator.get("sandbox") or "danger-full-access"
    approval = args.approval or coordinator.get("approval") or "never"
    search = bool(args.search or coordinator.get("search"))
    inline_tui = bool(args.inline_tui or coordinator.get("inline_tui"))

    handoff = write_coordinator_handoff(base, registry, reason)
    prompt_file = base / "tasks" / f"main-coordinator.recovery.{timestamp_slug()}.md"
    prompt = f"""You are the recovered main Codex coordinator for a tmux-managed autonomous multiprocess run.

The previous coordinator target was `{old_target or '-'}` and recovery reason is `{reason}`.

Use the `long-running-autonomous-project-management` and `tmux-codex-parallel-workers` skills. Treat this as a continuation of the same autonomous run, not a fresh project.

Immediate recovery protocol:

1. Read the shortest context pack first: {coordinator_context_pack_path(base)}
2. Read the compact coordinator memory: {coordinator_memory_path(base)}
3. Read the unified coordinator constraints: {constraints_file}
4. Read the handoff file: {handoff}
5. Read the schedule only when the compact memory is insufficient: {schedule_doc_path(base)}
6. Inspect existing workers with:
   python {MANAGER_PATH} --state-dir {base} list
   python {MANAGER_PATH} --state-dir {base} jobs
   python {MANAGER_PATH} --state-dir {base} progress --lines 20
   python {MANAGER_PATH} --state-dir {base} collect --lines 20
7. Reconstruct the mission, active workers, branch-manager hierarchy, resource ownership, open blockers, and next checkpoints from durable files.
8. Do not restart existing workers from scratch. Resume, redirect, stop, or collect them only after checking their progress/report/jobs.
9. Record the recovery with schedule-note and compact-memory, refresh consult context, and continue coordinating until the objective is complete or the user explicitly stops autonomous follow-up.
10. Keep coordinator context lean: consume context pack and compact memory first, and only load long logs or full captures when needed for diagnosis or final review.

Key files:

- State dir: {base}
- Context pack: {coordinator_context_pack_path(base)}
- Compact memory: {coordinator_memory_path(base)}
- Unified constraints: {constraints_file}
- Handoff: {handoff}
- Schedule: {schedule_doc_path(base)}
- Consultation context: {consult_context_path(base)}
- Worker registry: {registry_path(base)}
- Peer messages: {peer_messages_path(base)}
"""
    write_text(prompt_file, prompt)

    if args.dry_run:
        append_manager_log(base, f"recover-coordinator dry-run reason={reason} old_target={old_target} new_window={window}")
        append_schedule_event(base, "coordinator-recovery-dry-run", detail=f"Would recover coordinator old={old_target} new={session}:{window} reason={reason}")
        print(f"dry_run=true")
        print(f"would_launch={session}:{window}")
        print(f"prompt={prompt_file}")
        print(f"handoff={handoff}")
        return

    if args.kill_old and old_target and tmux_target_present(old_target):
        tmux("kill-pane", "-t", old_target, check=False)
        append_manager_log(base, f"recover-coordinator killed old target={old_target}")

    log_file = coordinator_log_path(base)
    status_file = coordinator_status_path(base)
    command = codex_command(
        mode="interactive",
        cwd=cwd,
        prompt_file=prompt_file,
        log_file=log_file,
        status_file=status_file,
        add_dirs=[base],
        model=model,
        reasoning_effort=reasoning_effort,
        profile=profile,
        sandbox=sandbox,
        approval=approval,
        search=search,
        inline_tui=inline_tui,
    )
    new_tmux_window(session, window, cwd, command)
    target = f"{session}:{window}"
    tmux("pipe-pane", "-o", "-t", target, f"cat >> {shlex.quote(str(log_file))}")
    time.sleep(args.startup_wait)
    send_prompt(
        target,
        "Please read and execute this coordinator recovery prompt file: "
        f"{prompt_file}\n"
        f"Start with the short context pack: {coordinator_context_pack_path(base)}\n"
        f"Then read the unified coordinator constraints: {constraints_file}\n"
        f"Then read the recovery handoff file: {handoff}\n"
        "Continue the existing autonomous multiprocess run; do not restart workers from scratch.",
    )

    previous_targets = list(coordinator.get("previous_targets", []))
    if old_target:
        previous_targets.append({"target": old_target, "ended_at": now_iso(), "reason": reason})
        previous_targets = previous_targets[-20:]
    coordinator.update(
        {
            "role": "main-coordinator",
            "target": target,
            "session": session,
            "window": window,
            "cwd": str(cwd),
            "model": model,
            "reasoning_effort": reasoning_effort,
            "best_model_default": not args.no_best_model,
            "profile": profile,
            "sandbox": sandbox,
            "approval": approval,
            "search": search,
            "inline_tui": inline_tui,
            "handoff_file": str(handoff),
            "prompt_file": str(prompt_file),
            "status_file": str(status_file),
            "log_file": str(log_file),
            "last_recovery_at": now_iso(),
            "last_recovery_reason": reason,
            "recovery_count": int(coordinator.get("recovery_count", 0)) + 1,
            "previous_targets": previous_targets,
            "updated_at": now_iso(),
        }
    )
    coordinator.setdefault("registered_at", now_iso())
    registry["coordinator"] = coordinator
    registry["session"] = session
    registry["updated_at"] = now_iso()
    save_registry(base, registry)
    write_text(status_file, json.dumps({"state": "launched", "target": target, "recovered_at": now_iso(), "reason": reason}, ensure_ascii=False) + "\n")
    append_manager_log(base, f"recover-coordinator old={old_target} new={target} reason={reason}")
    append_schedule_event(base, "coordinator-recovery", detail=f"Recovered coordinator old={old_target} new={target} reason={reason}")
    refresh_schedule_doc(base, registry)
    print(f"recovered_coordinator={target}")
    print(f"prompt={prompt_file}")
    print(f"handoff={handoff}")
    print_window_access(session, window)


def cmd_launch(args: argparse.Namespace) -> None:
    require_binary("tmux")
    require_binary("codex")
    base = state_dir(args.state_dir)
    requested_cwd = Path(args.cwd).expanduser().resolve()
    constraints_file = ensure_constraints_doc(base)

    name = safe_name(args.name)
    window = safe_name(args.window or f"cw-{name}")
    if window_exists(args.session, window):
        raise SystemExit(f"tmux window already exists: {args.session}:{window}")

    worker_kind = args.worker_kind
    mode = args.mode or ("interactive" if worker_kind in {"autonomous-experiment", "branch-manager"} else "exec")
    start_supervisor = args.start_supervisor or (worker_kind in {"autonomous-experiment", "branch-manager"} and not args.no_start_supervisor)
    task = read_task(args)
    git_meta = None
    cwd = requested_cwd
    if args.git_worktree:
        cwd, git_meta = create_git_worktree(base, name, requested_cwd, args)
    ensure_session(args.session, cwd)
    owned_paths = normalize_paths(cwd, args.owned_path)
    resources = args.resource or []
    model, reasoning_effort = resolve_model_settings(args)
    registry = load_registry(base)
    parent_worker = safe_name(args.parent_worker) if args.parent_worker else None
    if parent_worker:
        if parent_worker == name:
            raise SystemExit("A worker cannot be its own parent.")
        parent_record = registry.get("workers", {}).get(parent_worker)
        if not parent_record:
            raise SystemExit(f"unknown parent worker: {parent_worker}")
        if parent_record.get("worker_kind") != "branch-manager":
            raise SystemExit(f"parent worker must be worker_kind=branch-manager: {parent_worker}")
    if args.manager_scope and worker_kind != "branch-manager":
        raise SystemExit("--manager-scope is only valid for --worker-kind branch-manager.")
    check_launch_conflicts(
        registry,
        name=name,
        session=args.session,
        owned_paths=owned_paths,
        resources=resources,
        allow_conflict=args.allow_conflict,
    )
    task_dir = base / "tasks"
    log_dir = base / "logs"
    task_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = task_dir / f"{name}.prompt.md"
    log_file = log_dir / f"{name}.log"
    docs = create_worker_docs(
        base=base,
        name=name,
        session=args.session,
        window=window,
        worker_kind=worker_kind,
        cwd=cwd,
        task=task,
        write_scope=args.write_scope or [],
        owned_paths=owned_paths,
        resources=resources,
        git_meta=git_meta,
        model=model,
        reasoning_effort=reasoning_effort,
        notes=args.notes,
        parent_worker=parent_worker,
        manager_scope=args.manager_scope or [],
    )

    scope_lines = []
    if args.write_scope:
        scope_lines.append("Write scope:")
        scope_lines.extend(f"- {item}" for item in args.write_scope)
    else:
        scope_lines.append("Write scope: read-only unless the task explicitly requires edits.")
    if args.notes:
        scope_lines.append("Coordinator notes:")
        scope_lines.append(args.notes)
    if parent_worker:
        scope_lines.append(f"Parent worker: {parent_worker}")
    if args.manager_scope:
        scope_lines.append("Manager scope:")
        scope_lines.extend(f"- {item}" for item in args.manager_scope)
    if worker_kind == "branch-manager":
        scope_lines.extend(
            [
                "Branch manager worker rules:",
                "- You are a subordinate management worker for one major branch. The main coordinator delegates branch-level planning and first-pass integration to you.",
                "- Decompose the branch into bounded child workers, usually `--worker-kind autonomous-experiment` for real experiment branches.",
                f"- Launch child workers with `--parent-worker {name}` plus explicit `--owned-path`, `--resource`, write scope, and expected report.",
                "- You may send, interrupt, peer-send, collect, and schedule-note for child workers inside your assigned manager scope.",
                "- You may allow short factual peer communication among child workers, but scope/resource changes still require you or the main coordinator to record a decision.",
                "- Keep your own progress/report as the branch control document: child table, resources, current results, blockers, and next coordination decision.",
                "- Keep main-coordinator-facing updates compact. Do not paste raw child logs; cite child reports, captures, jobs, and artifacts.",
                "- Do not make final merge, promotion, user-facing conclusion, or cross-branch resource decisions unless the main coordinator explicitly delegates that decision.",
            ]
        )
    if worker_kind == "autonomous-experiment":
        scope_lines.extend(
            [
                "Autonomous experiment worker rules:",
                "- This is a visible interactive Codex worker. Keep the tmux window useful for human review: briefly state major intent before important actions, run short inspections visibly, and summarize decisions as they happen.",
                "- Own this experiment branch within the assigned write scope and resources. You may run, diagnose, and iterate experiments without waiting for the coordinator on every small step.",
                "- Long training/evaluation commands must run as background jobs with clear log files. Immediately register each job through the manager `job-add` command shown in the worker plan.",
                "- Do not hide the work only inside detached scripts. The tmux Codex pane should show what you are checking, launching, diagnosing, and deciding.",
                "- Redirect noisy command output to log/artifact files. In the tmux pane, inspect short tails, metric snippets, or focused error excerpts instead of dumping full logs or huge tables.",
                "- Keep progress updated at launch, after job registration, after failures, after metric checkpoints, and before handoff.",
                "- Write the final report with commands, logs, metrics, artifacts, failed attempts, changed files, and the next recommended action.",
                "- Do not modify coordinator-owned registry, status, schedule, or consultation files directly.",
            ]
        )

    prompt = "\n\n".join(
        [
            f"You are a {worker_kind} Codex worker launched by a coordinator in tmux.",
            "Do not revert edits made by the coordinator or other workers. Stay inside the assigned task and report changed files, commands run, results, blockers, and next recommended action before exiting.",
            f"Unified coordinator constraints: read and obey this file before doing anything else: {constraints_file}",
            "These constraints are higher priority than the worker-specific task unless the coordinator records an explicit override. Pay special attention to resource ownership, TensorBoard/dashboard safe ports, bind hosts, output paths, destructive cleanup, and background job registration.",
            "Update the progress file at key milestones and before completion. If you produce a longer result, write it into the report file.",
            "Coordinator context budget: keep progress/report updates and tmux replies concise. Do not paste raw logs, full diffs, long tables, or complete transcripts into coordinator-facing updates; write them to artifact/log files and cite paths with a short summary.",
            "Worker-to-worker communication is allowed only through manager-mediated `peer-send` messages. Use it for short factual evidence, blockers, or dependency notices; do not treat peer messages as permission to change assigned scope or resources.",
            "The worker state directory is mounted writable for progress/report/job registration. Do not edit workers.json, status files, schedule files, or registry files manually; use the manager commands when state changes are needed.",
            "Check the inbox directory before major transitions and after coordinator messages; it may contain queued instructions that are also sent through tmux.",
            "If you start any background process, register it with the manager using the job-add command shown in the worker plan.",
            f"Worker plan: {docs['workplan']}",
            f"Progress file: {docs['progress']}",
            f"Report file: {docs['report']}",
            f"Inbox directory: {docs['inbox']}",
            f"Background jobs file: {docs['jobs']}",
            f"Unified constraints file: {constraints_file}",
            "\n".join(scope_lines),
            f"Task:\n{task}",
        ]
    )
    prompt_file.write_text(prompt + "\n", encoding="utf-8")

    command = codex_command(
        mode=mode,
        cwd=cwd,
        prompt_file=prompt_file,
        log_file=log_file,
        status_file=docs["status"],
        add_dirs=[base],
        model=model,
        reasoning_effort=reasoning_effort,
        profile=args.profile,
        sandbox=args.sandbox,
        approval=args.approval,
        search=args.search,
        inline_tui=args.inline_tui,
    )
    new_tmux_window(args.session, window, cwd, command)
    target = f"{args.session}:{window}"
    if mode == "interactive":
        tmux("pipe-pane", "-o", "-t", target, f"cat >> {shlex.quote(str(log_file))}")
        time.sleep(args.startup_wait)
        send_prompt(
            target,
            "Please first read the unified coordinator constraints, then read and execute this worker prompt file.\n"
            f"Constraints: {constraints_file}\n"
            "Worker prompt: "
            f"{prompt_file}\n"
            "Keep progress/report concise, cite artifact paths for long evidence, and check the inbox directory for coordinator messages.",
        )

    registry = load_registry(base)
    registry["session"] = args.session
    registry["default_worker_model"] = DEFAULT_WORKER_MODEL
    registry["default_worker_reasoning"] = DEFAULT_WORKER_REASONING
    registry.setdefault("workers", {})[name] = {
        "name": name,
        "window": window,
        "session": args.session,
        "mode": mode,
        "worker_kind": worker_kind,
        "parent_worker": parent_worker,
        "manager_scope": args.manager_scope or [],
        "cwd": str(cwd),
        "prompt_file": str(prompt_file),
        "log_file": str(log_file),
        "workplan_file": str(docs["workplan"]),
        "progress_file": str(docs["progress"]),
        "report_file": str(docs["report"]),
        "inbox_dir": str(docs["inbox"]),
        "status_file": str(docs["status"]),
        "jobs_file": str(docs["jobs"]),
        "write_scope": args.write_scope or [],
        "owned_paths": owned_paths,
        "resources": resources,
        "git_worktree": git_meta,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "best_model_default": not args.no_best_model,
        "inline_tui": args.inline_tui,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    registry["updated_at"] = now_iso()
    save_registry(base, registry)
    append_manager_log(base, f"launch name={name} session={args.session} window={window} cwd={cwd}")
    append_schedule_event(
        base,
        "launch",
        worker=name,
        detail=f"Launched {mode} worker at {args.session}:{window}",
        data={"cwd": str(cwd), "resources": resources, "owned_paths": owned_paths, "parent_worker": parent_worker},
    )
    refresh_schedule_doc(base, registry)
    print(f"launched {name} at {args.session}:{window}")
    print(f"worker_kind={worker_kind}")
    if parent_worker:
        print(f"parent_worker={parent_worker}")
    print(f"mode={mode}")
    print_window_access(args.session, window)
    print(f"log={log_file}")
    print(f"progress={docs['progress']}")
    print(f"report={docs['report']}")
    if start_supervisor:
        start_supervisor_window(
            base,
            args.session,
            cwd,
            args.supervisor_interval,
            args.query_interactive,
            args.supervisor_lines,
            args.supervisor_query_interval,
            args.supervisor_refresh_schedule_interval,
            args.supervisor_progress_append_interval,
        )


def cmd_list(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    workers = registry.get("workers", {})
    if not workers:
        print("no registered workers")
        return
    for name, worker in sorted(workers.items()):
        session = worker.get("session", args.session)
        window = worker.get("window", "")
        status = effective_state(worker, args.session)
        status_data = read_status_file(worker) or {}
        exit_part = f" exit={status_data.get('exit_code')}" if "exit_code" in status_data else ""
        resources = ",".join(worker.get("resources", [])) or "-"
        parent = worker.get("parent_worker") or "main"
        print(f"{name}\t{status}{exit_part}\tkind={worker.get('worker_kind', 'standard')}\tparent={parent}\tmode={worker.get('mode', '-')}\t{session}:{window}\tresources={resources}\t{worker.get('cwd', '')}\t{worker.get('log_file', '')}")


def cmd_capture(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    worker = registry.get("workers", {}).get(safe_name(args.name))
    if not worker:
        raise SystemExit(f"unknown worker: {args.name}")
    target = f"{worker['session']}:{worker['window']}"
    if not window_exists(worker["session"], worker["window"]):
        raise SystemExit(f"worker window is not present: {target}")
    if args.log:
        log_file = Path(worker["log_file"])
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n".join(lines[-args.lines :]))
            return
    output = capture_target(target, args.lines)
    print(output.rstrip())


def cmd_send(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    worker = registry.get("workers", {}).get(safe_name(args.name))
    if not worker:
        raise SystemExit(f"unknown worker: {args.name}")
    target = f"{worker['session']}:{worker['window']}"
    if not window_exists(worker["session"], worker["window"]):
        raise SystemExit(f"worker window is not present: {target}")
    message = args.message
    if args.message_file:
        message = Path(args.message_file).expanduser().read_text(encoding="utf-8")
    if not message.strip():
        raise SystemExit("Provide a non-empty message or --message-file.")
    if args.via_inbox:
        inbox_file = write_inbox_message(worker, message)
        message = (
            f"Please read and follow the coordinator message in this inbox file: {inbox_file}\n"
            "After handling it, update your progress file with the action taken."
        )
    escape_after = getattr(args, "escape_after", False)
    send_prompt(target, message, escape_first=args.escape_first, escape_after=escape_after)
    append_manager_log(
        base,
        f"send name={worker['name']} escape_first={args.escape_first} escape_after={escape_after} "
        f"via_inbox={args.via_inbox} chars={len(message)}",
    )
    append_schedule_event(
        base,
        "send",
        worker=worker["name"],
        detail=(
            f"Sent coordinator instruction escape_first={args.escape_first} "
            f"escape_after={escape_after} via_inbox={args.via_inbox}"
        ),
    )
    refresh_schedule_doc(base, registry)


def cmd_interrupt_send(args: argparse.Namespace) -> None:
    args.escape_first = False
    args.escape_after = True
    cmd_send(args)


def cmd_peer_send(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    workers = registry.get("workers", {})
    source_name = safe_name(args.source)
    target_name = safe_name(args.target)
    source = workers.get(source_name)
    target_worker = workers.get(target_name)
    if not source:
        raise SystemExit(f"unknown source worker: {args.source}")
    if not target_worker:
        raise SystemExit(f"unknown target worker: {args.target}")
    if source_name == target_name:
        raise SystemExit("source and target workers must be different.")
    message = args.message
    if args.message_file:
        message = Path(args.message_file).expanduser().read_text(encoding="utf-8")
    if not message.strip():
        raise SystemExit("Provide a non-empty --message or --message-file.")
    inbox_file = write_inbox_message(
        target_worker,
        message,
        title="Peer Worker Message",
        source=source_name,
        target=target_name,
    )
    record = {
        "timestamp": now_iso(),
        "source": source_name,
        "target": target_name,
        "inbox_file": str(inbox_file),
        "summary": one_line(message, 220),
        "notified": bool(args.notify),
    }
    append_text(peer_messages_path(base), json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    append_text(
        Path(source.get("progress_file", base / "progress" / f"{source_name}.md")),
        f"\n- {now_iso()} Peer message sent to `{target_name}`: `{inbox_file}`\n",
    )
    append_text(
        Path(target_worker.get("progress_file", base / "progress" / f"{target_name}.md")),
        f"\n- {now_iso()} Peer message received from `{source_name}`: `{inbox_file}`\n",
    )
    if args.notify:
        session = target_worker["session"]
        window = target_worker["window"]
        if not window_exists(session, window):
            print(f"warning: target worker window is not present: {session}:{window}; message written to {inbox_file}", file=sys.stderr)
        else:
            send_prompt(
                f"{session}:{window}",
                f"Peer message from {source_name} is available at: {inbox_file}\n"
                "Read it at the next safe checkpoint. Treat it as factual evidence or a dependency notice; do not change assigned scope/resources unless your branch manager or coordinator records a decision.",
                escape_first=args.escape_first,
                escape_after=args.escape_after,
            )
    append_manager_log(base, f"peer-send source={source_name} target={target_name} notify={args.notify} inbox={inbox_file}")
    append_schedule_event(
        base,
        "peer-send",
        worker=source_name,
        detail=f"Peer message {source_name} -> {target_name}; inbox={inbox_file}",
        data={"source": source_name, "target": target_name, "inbox_file": str(inbox_file), "notified": bool(args.notify)},
    )
    refresh_schedule_doc(base, registry)
    print(f"inbox={inbox_file}")


def cmd_progress(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    workers = registry.get("workers", {})
    names = [safe_name(args.name)] if args.name else sorted(workers)
    if not names:
        print("no registered workers")
        return
    for name in names:
        worker = workers.get(name)
        if not worker:
            print(f"unknown worker: {name}")
            continue
        progress = Path(worker.get("progress_file", ""))
        print(f"=== {name} ===")
        if progress.exists():
            lines = progress.read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n".join(lines[-args.lines :]))
        else:
            print("no progress file")


def resolve_worker(base: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = load_registry(base)
    worker = registry.get("workers", {}).get(safe_name(name))
    if not worker:
        raise SystemExit(f"unknown worker: {name}")
    return registry, worker


def cmd_job_add(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry, worker = resolve_worker(base, args.worker)
    name = safe_name(args.name or f"pid-{args.pid}")
    path = jobs_path_for(base, worker)
    data = load_jobs(path, worker["name"])
    job = {
        "worker": worker["name"],
        "name": name,
        "pid": args.pid,
        "kind": args.kind,
        "command": args.command or "",
        "log": str(Path(args.log).expanduser().resolve()) if args.log else "",
        "resources": args.resource or [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    data["jobs"] = [existing for existing in data.get("jobs", []) if existing.get("name") != name and existing.get("pid") != args.pid]
    data["jobs"].append(job)
    save_jobs(path, data)
    append_manager_log(base, f"job-add worker={worker['name']} name={name} pid={args.pid}")
    append_schedule_event(
        base,
        "job-add",
        worker=worker["name"],
        detail=f"Registered job {name} pid={args.pid}",
        data={"resources": args.resource or [], "log": job["log"]},
    )
    refresh_schedule_doc(base, registry)
    print(job_line(job))


def cmd_jobs(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    workers = registry.get("workers", {})
    names = [safe_name(args.worker)] if args.worker else sorted(workers)
    if not names:
        print("no registered workers")
        return
    for name in names:
        worker = workers.get(name)
        if not worker:
            print(f"unknown worker: {name}")
            continue
        data = load_jobs(jobs_path_for(base, worker), name)
        for job in data.get("jobs", []):
            print(job_line(job))


def cmd_job_stop(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    _, worker = resolve_worker(base, args.worker)
    path = jobs_path_for(base, worker)
    data = load_jobs(path, worker["name"])
    signum = getattr(signal, f"SIG{args.signal.upper()}", None)
    if signum is None:
        raise SystemExit(f"unknown signal: {args.signal}")
    matched = []
    for job in data.get("jobs", []):
        if args.all or (args.pid and int(job.get("pid", 0)) == args.pid) or (args.name and job.get("name") == args.name):
            matched.append(job)
    if not matched:
        raise SystemExit("no matching jobs")
    for job in matched:
        pid = int(job.get("pid", 0))
        if pid and pid_alive(pid):
            os.kill(pid, signum)
            job["stopped_at"] = now_iso()
            job["stop_signal"] = args.signal.upper()
            job["updated_at"] = now_iso()
            print(f"sent SIG{args.signal.upper()} to pid={pid} job={job.get('name')}")
        else:
            print(f"job not alive: {job_line(job)}")
    save_jobs(path, data)
    append_manager_log(base, f"job-stop worker={worker['name']} count={len(matched)} signal={args.signal.upper()}")
    append_schedule_event(
        base,
        "job-stop",
        worker=worker["name"],
        detail=f"Sent SIG{args.signal.upper()} to {len(matched)} job(s)",
    )
    refresh_schedule_doc(base, load_registry(base))


def prune_old_files(directory: Path, keep: int) -> None:
    if keep <= 0 or not directory.exists():
        return
    files = sorted((item for item in directory.iterdir() if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def supervise_once(base: Path, registry: dict[str, Any], args: argparse.Namespace, last_query: dict[str, float]) -> None:
    for name, worker in sorted(registry.get("workers", {}).items()):
        session = worker.get("session", args.session)
        window = worker.get("window", "")
        if not window_exists(session, window):
            continue
        target = f"{session}:{window}"
        stamp = now_iso()
        captured = capture_target(target, args.lines)
        capture_hash = hashlib.sha256(captured.encode("utf-8", errors="replace")).hexdigest()
        status_path = Path(worker.get("status_file") or base / "status" / f"{name}.json")
        status_data = read_status_file(worker) or {}
        now_ts = time.time()
        previous_state = status_data.get("state")
        capture_changed = status_data.get("capture_hash") != capture_hash
        if capture_changed:
            status_data["last_change_at"] = stamp
            status_data["last_change_ts"] = now_ts
        last_change_ts = float(status_data.get("last_change_ts", now_ts))
        state = "stalled" if now_ts - last_change_ts >= args.stall_seconds else "running"
        status_data.update(
            {
                "state": state,
                "last_capture_at": stamp,
                "last_capture_ts": now_ts,
                "capture_hash": capture_hash,
                "stalled_seconds": int(max(0, now_ts - last_change_ts)),
            }
        )
        progress_file = Path(worker.get("progress_file") or base / "progress" / f"{name}.md")
        capture_dir = base / "captures" / name
        supervisor_capture = capture_dir / f"{timestamp_slug()}.txt"
        write_text(supervisor_capture, captured + "\n")
        write_text(capture_dir / "latest.txt", captured + "\n")
        prune_old_files(capture_dir, args.capture_retention)

        last_progress_append_ts = float(status_data.get("last_progress_append_ts", 0))
        progress_due = now_ts - last_progress_append_ts >= args.progress_append_interval
        should_append_progress = capture_changed or state != previous_state or progress_due or args.once
        if should_append_progress:
            status_data["last_progress_append_at"] = stamp
            status_data["last_progress_append_ts"] = now_ts
            append_text(
                progress_file,
                f"\n## Supervisor Capture - {stamp}\n\n"
                f"- Target: {target}\n"
                f"- State: {state}\n"
                f"- Capture changed: {capture_changed}\n"
                f"- Latest capture: {supervisor_capture}\n",
            )
        write_text(status_path, json.dumps(status_data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        append_manager_log(base, f"supervise-capture name={name} target={target} lines={args.lines}")

        due = time.time() - last_query.get(name, 0) >= args.query_interval
        query_allowed = args.query_interactive and worker.get("mode") == "interactive" and due
        if args.query_only_stalled and state != "stalled":
            query_allowed = False
        if query_allowed:
            query = args.query_prompt
            send_prompt(target, query, escape_first=args.query_escape_first)
            last_query[name] = time.time()
            append_manager_log(base, f"supervise-query name={name} chars={len(query)}")
            time.sleep(args.response_wait)
            response = capture_target(target, args.lines)
            response_capture = capture_dir / f"{timestamp_slug()}.query-response.txt"
            write_text(response_capture, response + "\n")
            write_text(capture_dir / "latest-query-response.txt", response + "\n")
            append_text(
                progress_file,
                f"\n## Supervisor Query - {now_iso()}\n\n"
                f"- Query sent and response captured.\n"
                f"- Response capture: {response_capture}\n",
            )
            if args.continue_prompt:
                send_prompt(target, args.continue_prompt)
                append_manager_log(base, f"supervise-continue name={name} chars={len(args.continue_prompt)}")


def cmd_supervise(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    if not args.once and not args.allow_foreground_loop:
        raise SystemExit(
            "Refusing to run an unbounded supervisor loop in the foreground. "
            "Use start-supervisor to run it in tmux, or pass --allow-foreground-loop only from a managed tmux window."
        )
    print(f"supervising state_dir={base} session={args.session} interval={args.interval}")
    last_query: dict[str, float] = {}
    started_at = now_iso()
    last_schedule_refresh_ts = 0.0
    cycle = 0
    try:
        while True:
            cycle += 1
            registry = load_registry(base)
            loop_stamp = now_iso()
            write_text(
                supervisor_status_path(base),
                json.dumps(
                    {
                        "state": "running",
                        "pid": os.getpid(),
                        "session": args.session,
                        "started_at": started_at,
                        "last_loop_at": loop_stamp,
                        "cycle": cycle,
                        "interval": args.interval,
                        "once": args.once,
                    },
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n",
            )
            supervise_once(base, registry, args, last_query)
            now_ts = time.time()
            if args.once or now_ts - last_schedule_refresh_ts >= args.refresh_schedule_interval:
                refresh_schedule_doc(base, load_registry(base))
                last_schedule_refresh_ts = now_ts
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        write_text(
            supervisor_status_path(base),
            json.dumps(
                {
                    "state": "completed-once" if args.once else "exited",
                    "pid": os.getpid(),
                    "session": args.session,
                    "started_at": started_at,
                    "exited_at": now_iso(),
                    "cycle": cycle,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
        )


def start_supervisor_window(
    base: Path,
    session: str,
    cwd: Path,
    interval: int,
    ask: bool,
    lines: int,
    query_interval: int,
    refresh_schedule_interval: int,
    progress_append_interval: int,
) -> None:
    require_binary("tmux")
    ensure_session(session, cwd)
    window = "cw-supervisor"
    if window_exists(session, window):
        print(f"supervisor already present at {session}:{window}")
        return
    command = supervisor_command(base, session, interval, ask, lines, query_interval, refresh_schedule_interval, progress_append_interval)
    new_tmux_window(session, window, cwd, command)
    append_manager_log(base, f"start-supervisor session={session} window={window} interval={interval} ask={ask}")
    append_schedule_event(base, "start-supervisor", detail=f"Started supervisor at {session}:{window} interval={interval} ask={ask}")
    refresh_schedule_doc(base, load_registry(base))
    print(f"supervisor started at {session}:{window}")
    print_window_access(session, window)


def cmd_start_supervisor(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    cwd = Path(args.cwd).expanduser().resolve()
    start_supervisor_window(
        base,
        args.session,
        cwd,
        args.interval,
        args.query_interactive,
        args.lines,
        args.query_interval,
        args.refresh_schedule_interval,
        args.progress_append_interval,
    )


def start_health_supervisor_window(
    base: Path,
    session: str,
    cwd: Path,
    interval: int,
    lines: int,
    stable_seconds: int,
    cooldown: int,
    watch_targets: list[str],
    observe_targets: list[str],
    no_workers: bool,
    no_coordinator: bool,
    restart_main_on_context_full: bool,
    restart_main_when_missing: bool,
    keep_old_main: bool,
    dry_run: bool,
    escape_after: bool,
    recovery_prompt: str | None,
    force: bool,
) -> None:
    require_binary("tmux")
    ensure_session(session, cwd)
    window = "cw-health-supervisor"
    if window_exists(session, window):
        if not force:
            print(f"health supervisor already present at {session}:{window}")
            return
        tmux("kill-window", "-t", f"{session}:{window}", check=False)
        time.sleep(0.5)
    command = health_supervisor_command(
        base,
        session,
        interval,
        lines,
        stable_seconds,
        cooldown,
        watch_targets,
        observe_targets,
        no_workers,
        no_coordinator,
        restart_main_on_context_full,
        restart_main_when_missing,
        keep_old_main,
        dry_run,
        escape_after,
        recovery_prompt,
    )
    new_tmux_window(session, window, cwd, command)
    append_manager_log(
        base,
        f"start-health-supervisor session={session} window={window} interval={interval} "
        f"stable_seconds={stable_seconds} cooldown={cooldown} watch_targets={watch_targets} observe_targets={observe_targets} "
        f"no_workers={no_workers} no_coordinator={no_coordinator} "
        f"restart_main_on_context_full={restart_main_on_context_full} restart_main_when_missing={restart_main_when_missing} "
        f"keep_old_main={keep_old_main} dry_run={dry_run}",
    )
    append_schedule_event(
        base,
        "start-health-supervisor",
        detail=(
            f"Started health supervisor at {session}:{window} interval={interval} "
            f"stable_seconds={stable_seconds} cooldown={cooldown} watch_targets={watch_targets} "
            f"observe_targets={observe_targets} no_workers={no_workers} no_coordinator={no_coordinator} "
            f"restart_main_on_context_full={restart_main_on_context_full} restart_main_when_missing={restart_main_when_missing} "
            f"keep_old_main={keep_old_main} dry_run={dry_run}"
        ),
    )
    refresh_schedule_doc(base, load_registry(base))
    print(f"health supervisor started at {session}:{window}")
    print_window_access(session, window)


def cmd_start_health_supervisor(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    cwd = Path(args.cwd).expanduser().resolve()
    start_health_supervisor_window(
        base,
        args.session,
        cwd,
        args.interval,
        args.lines,
        args.stable_seconds,
        args.cooldown,
        args.watch_target or [],
        args.observe_target or [],
        args.no_workers,
        args.no_coordinator,
        args.restart_main_on_context_full,
        args.restart_main_when_missing,
        args.keep_old_main,
        args.dry_run,
        args.escape_after,
        args.recovery_prompt,
        args.force,
    )


def cmd_stop_health_supervisor(args: argparse.Namespace) -> None:
    require_binary("tmux")
    base = state_dir(args.state_dir)
    window = args.window or "cw-health-supervisor"
    target = f"{args.session}:{window}"
    result = tmux("kill-window", "-t", target, check=False)
    append_manager_log(base, f"stop-health-supervisor target={target} status={result.returncode}")
    append_schedule_event(base, "stop-health-supervisor", detail=f"Stopped health supervisor target={target} status={result.returncode}")
    refresh_schedule_doc(base, load_registry(base))
    print(f"stopped health supervisor target={target} status={result.returncode}")


def cmd_resume(args: argparse.Namespace) -> None:
    require_binary("tmux")
    require_binary("codex")
    base = state_dir(args.state_dir)
    constraints_file = ensure_constraints_doc(base)
    registry, worker = resolve_worker(base, args.name)
    old_target = f"{worker['session']}:{worker['window']}"
    if window_exists(worker["session"], worker["window"]) and not args.force:
        raise SystemExit(f"worker window is still present: {old_target}; use --force only if you intentionally want another window")
    cwd = Path(worker["cwd"]).expanduser().resolve()
    ensure_session(args.session or worker["session"], cwd)
    session = args.session or worker["session"]
    window = safe_name(args.window or worker.get("window") or f"cw-{worker['name']}")
    if window_exists(session, window):
        window = safe_name(f"{window}-r{dt.datetime.now().strftime('%H%M%S')}")
    prompt_file = base / "tasks" / f"{worker['name']}.resume.{timestamp_slug()}.md"
    resume_prompt = "\n\n".join(
        [
            "You are resuming a tmux-managed Codex worker after interruption or coordinator restart.",
            f"Before resuming work, read and obey the unified coordinator constraints: {constraints_file}",
            f"Worker kind: {worker.get('worker_kind', 'standard')}",
            f"Worker plan: {worker.get('workplan_file')}",
            f"Progress file: {worker.get('progress_file')}",
            f"Report file: {worker.get('report_file')}",
            f"Inbox directory: {worker.get('inbox_dir')}",
            f"Background jobs file: {worker.get('jobs_file')}",
            f"Unified constraints file: {constraints_file}",
            "The worker state directory is mounted writable for progress/report/job registration. Do not edit workers.json, status files, schedule files, or registry files manually.",
            "Coordinator context budget: keep progress/report updates concise, write long evidence to artifact/log files, and cite paths with short summaries.",
            "If this is an autonomous-experiment worker, keep the tmux Codex pane useful as a visible operation trace: briefly state major intent before important actions, register long-running jobs, and update progress/report at checkpoints.",
            "Read those artifacts first, summarize the current state in the progress file, then continue the unfinished task. Do not redo completed work unless necessary.",
        ]
    )
    write_text(prompt_file, resume_prompt + "\n")
    status_file = Path(worker.get("status_file") or base / "status" / f"{worker['name']}.json")
    write_text(status_file, json.dumps({"state": "resuming", "resumed_at": now_iso()}, ensure_ascii=False) + "\n")
    mode = args.mode or worker.get("mode", "exec")
    model, reasoning_effort = resolve_model_settings(args)
    inline_tui = bool(args.inline_tui or worker.get("inline_tui"))
    command = codex_command(
        mode=mode,
        cwd=cwd,
        prompt_file=prompt_file,
        log_file=Path(worker["log_file"]),
        status_file=status_file,
        add_dirs=[base],
        model=model,
        reasoning_effort=reasoning_effort,
        profile=args.profile,
        sandbox=args.sandbox or "workspace-write",
        approval=args.approval or "never",
        search=args.search,
        inline_tui=inline_tui,
    )
    new_tmux_window(session, window, cwd, command)
    target = f"{session}:{window}"
    if mode == "interactive":
        tmux("pipe-pane", "-o", "-t", target, f"cat >> {shlex.quote(str(worker['log_file']))}")
        time.sleep(args.startup_wait)
        send_prompt(target, f"Please first read unified coordinator constraints: {constraints_file}\nThen read and execute this resume prompt file: {prompt_file}")
    worker["session"] = session
    worker["window"] = window
    worker["mode"] = mode
    worker["model"] = model
    worker["reasoning_effort"] = reasoning_effort
    worker["best_model_default"] = not args.no_best_model
    worker["inline_tui"] = inline_tui
    worker["updated_at"] = now_iso()
    worker["resume_prompt_file"] = str(prompt_file)
    worker.pop("stopped_at", None)
    registry["default_worker_model"] = DEFAULT_WORKER_MODEL
    registry["default_worker_reasoning"] = DEFAULT_WORKER_REASONING
    registry["updated_at"] = now_iso()
    save_registry(base, registry)
    append_manager_log(base, f"resume name={worker['name']} session={session} window={window}")
    append_schedule_event(base, "resume", worker=worker["name"], detail=f"Resumed worker at {session}:{window}")
    refresh_schedule_doc(base, registry)
    print(f"resumed {worker['name']} at {session}:{window}")
    print_window_access(session, window)


def worker_report_tail(worker: dict[str, Any], key: str, lines: int) -> str:
    path = Path(worker.get(key, ""))
    if not path.exists():
        return "missing"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def cmd_collect(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    out = Path(args.output).expanduser().resolve() if args.output else base / "reports" / f"COORDINATOR_SUMMARY_{timestamp_slug()}.md"
    parts = [f"# Codex Tmux Worker Collection\n\nGenerated: {now_iso()}\nState dir: {base}\n"]
    peer_messages = load_peer_messages(base, 20)
    if peer_messages:
        parts.append("\n## Recent Peer Messages\n\n")
        for item in peer_messages:
            parts.append(
                f"- {item.get('timestamp', '')} {item.get('source', '')} -> {item.get('target', '')}: "
                f"{one_line(item.get('summary', ''), 160)} ({item.get('inbox_file', '')})\n"
            )
    for name, worker in sorted(registry.get("workers", {}).items()):
        status = effective_state(worker, args.session)
        jobs = load_jobs(jobs_path_for(base, worker), name).get("jobs", [])
        parts.append(f"\n## {name}\n\n")
        parts.append(f"- State: {status}\n")
        parts.append(f"- Worker kind: {worker.get('worker_kind', 'standard')}\n")
        parts.append(f"- Parent worker: {worker.get('parent_worker') or 'main-coordinator'}\n")
        parts.append(f"- Mode: {worker.get('mode', '-')}\n")
        parts.append(f"- Target: {worker.get('session')}:{worker.get('window')}\n")
        parts.append(f"- CWD: {worker.get('cwd')}\n")
        parts.append(f"- Resources: {', '.join(worker.get('resources', [])) or '-'}\n")
        parts.append(f"- Manager scope: {', '.join(worker.get('manager_scope', [])) or '-'}\n")
        if worker.get("git_worktree"):
            meta = worker["git_worktree"]
            parts.append(f"- Git worktree: {meta.get('worktree_path')} branch={meta.get('branch')}\n")
        parts.append("\n### Jobs\n\n")
        parts.append("\n".join(job_line(job) for job in jobs) if jobs else "none")
        parts.append("\n\n### Progress Tail\n\n```text\n")
        parts.append(worker_report_tail(worker, "progress_file", args.lines))
        parts.append("\n```\n\n### Report Tail\n\n```text\n")
        parts.append(worker_report_tail(worker, "report_file", args.lines))
        parts.append("\n```\n\n")
        cwd = Path(worker.get("cwd", "."))
        if cwd.exists():
            parts.append(git_summary(cwd))
            parts.append("\n")
    write_text(out, "".join(parts))
    append_schedule_event(base, "collect", detail=f"Wrote coordinator collection summary to {out}")
    refresh_schedule_doc(base, registry)
    print(out)


def cmd_compact_memory(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    if args.mission:
        registry["mission"] = args.mission
        registry["updated_at"] = now_iso()
        save_registry(base, registry)
    has_note = any([args.note, args.decision, args.next_action, args.mission])
    if has_note:
        append_memory_event(
            base,
            "compact-memory",
            note=args.note or "",
            decision=args.decision or "",
            next_action=args.next_action or "",
            reason=args.reason,
        )
        append_schedule_event(
            base,
            "compact-memory",
            detail="; ".join(
                part
                for part in [
                    f"reason={args.reason}" if args.reason else "",
                    f"note={one_line(args.note or '', 120)}" if args.note else "",
                    f"decision={one_line(args.decision or '', 120)}" if args.decision else "",
                    f"next={one_line(args.next_action or '', 120)}" if args.next_action else "",
                    f"mission={one_line(args.mission or '', 120)}" if args.mission else "",
                ]
                if part
            ),
        )
    memory_path, context_pack_path = refresh_compact_memory(base, registry, reason=args.reason)
    refresh_consult_context(base, registry)
    write_coordinator_handoff(base, registry, reason=f"compact-memory:{args.reason}")
    if args.print:
        target = context_pack_path if args.context_pack else memory_path
        print(target.read_text(encoding="utf-8", errors="replace"))
    else:
        print(f"memory={memory_path}")
        print(f"context_pack={context_pack_path}")


def cmd_constraints(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    path = ensure_constraints_doc(base)
    changed = False
    if args.reset_defaults:
        write_text(path, default_constraints_text())
        append_constraints_event(base, "reset-defaults", f"Reset constraints to defaults at {path}")
        append_schedule_event(base, "constraints", detail="Reset unified coordinator constraints to defaults.")
        changed = True
    if args.set_file:
        source = Path(args.set_file).expanduser().resolve()
        write_text(path, source.read_text(encoding="utf-8"))
        append_constraints_event(base, "set-file", f"Replaced constraints from {source}", {"source": str(source)})
        append_schedule_event(base, "constraints", detail=f"Replaced unified coordinator constraints from {source}.")
        changed = True
    updates: list[str] = []
    for item in args.append or []:
        if item.strip():
            updates.append(item.strip())
    if args.tensorboard_port_range:
        updates.append(
            "TensorBoard/dashboard constraint: use only coordinator-assigned safe ports in "
            f"`{args.tensorboard_port_range}`; bind to `127.0.0.1` by default; register the chosen port as "
            "`--resource port:<PORT>` or in the worker job/progress record; do not kill an existing port owner without a coordinator decision."
        )
    if updates:
        section = ["", f"## Coordinator Constraint Update - {now_iso()}", ""]
        section.extend(f"- {item}" for item in updates)
        section.append("")
        append_text(path, "\n".join(section))
        append_constraints_event(base, "append", " | ".join(one_line(item, 160) for item in updates), {"updates": updates})
        append_schedule_event(base, "constraints", detail="Updated unified coordinator constraints: " + " | ".join(one_line(item, 160) for item in updates))
        changed = True
    registry = load_registry(base)
    if changed:
        refresh_schedule_doc(base, registry)
    else:
        refresh_compact_memory(base, registry, reason="constraints-read")
        refresh_consult_context(base, registry)
        write_coordinator_handoff(base, registry, reason="constraints-read")
    if args.print or not changed:
        print(path.read_text(encoding="utf-8", errors="replace"))
    else:
        print(path)


def cmd_schedule(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    path = refresh_schedule_doc(base, registry)
    if args.print:
        print(path.read_text(encoding="utf-8", errors="replace"))
    else:
        print(path)


def cmd_schedule_note(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    if args.mission:
        registry["mission"] = args.mission
        registry["updated_at"] = now_iso()
        save_registry(base, registry)
    detail_parts = []
    if args.note:
        detail_parts.append(args.note)
    if args.decision:
        detail_parts.append(f"Decision: {args.decision}")
    if args.next_action:
        detail_parts.append(f"Next action: {args.next_action}")
    detail = "\n".join(detail_parts).strip()
    if not detail and not args.mission:
        raise SystemExit("Provide --note, --decision, --next-action, or --mission.")
    append_schedule_event(
        base,
        args.event,
        worker=safe_name(args.worker) if args.worker else None,
        detail=detail or f"Mission updated: {args.mission}",
    )
    if args.note or args.decision or args.next_action or args.mission:
        append_memory_event(
            base,
            "schedule-note",
            note=args.note or (f"Mission updated: {args.mission}" if args.mission else ""),
            decision=args.decision or "",
            next_action=args.next_action or "",
            reason=args.event,
        )
    path = refresh_schedule_doc(base, registry)
    print(path)


def write_consult_prompt(base: Path, registry: dict[str, Any]) -> Path:
    path = consult_prompt_path(base)
    constraints_file = ensure_constraints_doc(base)
    prompt = f"""# Codex Tmux User Consultation Worker

You are the dedicated read-only user consultation Codex worker for a tmux-managed autonomous run.

Your job:

- Answer user questions about the current coordinator mission, worker layout, scheduling decisions, logs, results, resources, blockers, and evidence paths.
- Keep answers grounded in the local state files. Before each answer, re-read the consultation context, coordinator schedule, and unified coordinator constraints.
- Default to Chinese unless the user asks otherwise.
- Give concrete paths and commands when helpful.
- Keep answers compact by default. Summarize long reports/logs and cite paths or manager commands instead of pasting long excerpts.

Hard limits:

- Do not start, stop, resume, launch, or interrupt workers.
- Do not edit project files, registry files, schedules, reports, source code, or experiment artifacts.
- Do not make final integration or promotion decisions. Attribute such decisions to the coordinator artifacts when they already exist.
- If the user asks you to execute or mutate state, explain that the main coordinator or manager command should do it.
- If the requested detail is missing, say which file or worker report is missing instead of guessing.

Primary files to read:

- Consultation context: {consult_context_path(base)}
- Unified coordinator constraints: {constraints_file}
- Coordinator schedule: {schedule_doc_path(base)}
- Worker registry: {registry_path(base)}
- Schedule events: {schedule_events_path(base)}

Useful read-only commands:

```bash
python {MANAGER_PATH} --state-dir {base} consult-context --print
python {MANAGER_PATH} --state-dir {base} constraints --print
python {MANAGER_PATH} --state-dir {base} schedule --print
python {MANAGER_PATH} --state-dir {base} list
python {MANAGER_PATH} --state-dir {base} jobs
python {MANAGER_PATH} --state-dir {base} progress <worker> --lines 40
python {MANAGER_PATH} --state-dir {base} capture <worker> --log --lines 80
```

Current mission:

{registry.get("mission", "未设置")}
"""
    write_text(path, prompt)
    return path


def cmd_start_consult(args: argparse.Namespace) -> None:
    require_binary("tmux")
    require_binary("codex")
    base = state_dir(args.state_dir)
    cwd = Path(args.cwd).expanduser().resolve()
    session = args.session
    window = safe_name(args.window)
    ensure_session(session, cwd)
    if window_exists(session, window):
        if not args.force:
            registry = load_registry(base)
            consult = registry.get("consult") or {}
            if consult.get("session") != session or consult.get("window") != window:
                raise SystemExit(f"tmux window already exists but is not the registered consult worker: {session}:{window}; use --force or --window")
            registry["session"] = session
            registry["state_dir"] = str(base)
            registry.setdefault("workers", {})
            registry["updated_at"] = now_iso()
            save_registry(base, registry)
            schedule_path = refresh_schedule_doc(base, registry)
            print(f"consult worker already present at {session}:{window}")
            print(f"context={consult_context_path(base)}")
            print(f"schedule={schedule_path}")
            return
        tmux("kill-window", "-t", f"{session}:{window}")

    registry = load_registry(base)
    registry["session"] = session
    registry["state_dir"] = str(base)
    registry["default_worker_model"] = DEFAULT_WORKER_MODEL
    registry["default_worker_reasoning"] = DEFAULT_WORKER_REASONING
    registry.setdefault("workers", {})
    model, reasoning_effort = resolve_model_settings(args)
    prompt_file = write_consult_prompt(base, registry)
    log_file = consult_log_path(base)
    status_file = consult_status_path(base)
    write_text(status_file, json.dumps({"state": "launching", "started_at": now_iso()}, ensure_ascii=False) + "\n")

    command = codex_command(
        mode="interactive",
        cwd=cwd,
        prompt_file=prompt_file,
        log_file=log_file,
        status_file=status_file,
        add_dirs=None,
        model=model,
        reasoning_effort=reasoning_effort,
        profile=args.profile,
        sandbox=args.sandbox,
        approval=args.approval,
        search=args.search,
        inline_tui=args.inline_tui,
    )
    new_tmux_window(session, window, cwd, command)
    target = f"{session}:{window}"
    tmux("pipe-pane", "-o", "-t", target, f"cat >> {shlex.quote(str(log_file))}")
    write_text(status_file, json.dumps({"state": "running", "started_at": now_iso()}, ensure_ascii=False) + "\n")

    registry["consult"] = {
        "role": "user-consultation",
        "window": window,
        "session": session,
        "cwd": str(cwd),
        "prompt_file": str(prompt_file),
        "context_file": str(consult_context_path(base)),
        "schedule_file": str(schedule_doc_path(base)),
        "log_file": str(log_file),
        "status_file": str(status_file),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": args.sandbox,
        "approval": args.approval,
        "inline_tui": args.inline_tui,
        "best_model_default": not args.no_best_model,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    registry["updated_at"] = now_iso()
    save_registry(base, registry)
    append_manager_log(base, f"start-consult session={session} window={window} cwd={cwd}")
    append_schedule_event(base, "start-consult", detail=f"Started read-only consultation worker at {target}")
    refresh_schedule_doc(base, registry)

    time.sleep(args.startup_wait)
    send_prompt(
        target,
        "Please read this consultation startup prompt first: "
        f"{prompt_file}\n"
        f"Then read the current consultation context: {consult_context_path(base)}\n"
        f"Before every user-facing answer, re-read {consult_context_path(base)}, {coordinator_constraints_path(base)}, and {schedule_doc_path(base)}. "
        "Stay read-only, answer compactly by default, and wait for user questions.",
    )
    print(f"consult worker started at {target}")
    print_window_access(session, window)
    print(f"context={consult_context_path(base)}")
    print(f"schedule={schedule_doc_path(base)}")
    print(f"log={log_file}")


def cmd_consult_sync(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    detail = args.message or "Refreshed consultation context."
    append_schedule_event(base, "consult-sync", detail=detail)
    schedule_path = refresh_schedule_doc(base, registry)
    context_path = consult_context_path(base)
    consult = registry.get("consult") or {}
    if args.notify:
        session = consult.get("session", registry.get("session", args.session))
        window = consult.get("window", "")
        if not window or not window_exists(session, window):
            raise SystemExit("consult worker is not present; run start-consult first or omit --notify")
        send_prompt(
            f"{session}:{window}",
            f"Coordinator consultation context was refreshed at {now_iso()}.\n"
            f"Please re-read: {context_path}\n"
            f"Also re-read unified coordinator constraints: {coordinator_constraints_path(base)}\n"
            f"Also re-read: {schedule_path}\n"
            f"Coordinator note: {detail}\n"
            "Do not execute any mutations; continue answering user questions from the refreshed context.",
            escape_first=args.escape_first,
        )
    print(f"context={context_path}")
    print(f"schedule={schedule_path}")


def cmd_consult_context(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    refresh_schedule_doc(base, registry)
    path = consult_context_path(base)
    if args.print:
        print(path.read_text(encoding="utf-8", errors="replace"))
    else:
        print(path)


def cmd_stop_consult(args: argparse.Namespace) -> None:
    require_binary("tmux")
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    consult = registry.get("consult")
    if not consult:
        print("no consult worker registered")
        return
    session = consult.get("session", registry.get("session", args.session))
    window = consult.get("window", args.window or "cw-consult")
    target = f"{session}:{window}"
    if window and window_exists(session, window):
        tmux("kill-window", "-t", target)
    status_file = consult.get("status_file")
    if status_file:
        write_text(Path(status_file), json.dumps({"state": "stopped", "stopped_at": now_iso()}, ensure_ascii=False) + "\n")
    consult["stopped_at"] = now_iso()
    consult["updated_at"] = now_iso()
    registry["consult"] = consult
    registry["updated_at"] = now_iso()
    save_registry(base, registry)
    append_manager_log(base, f"stop-consult target={target}")
    append_schedule_event(base, "stop-consult", detail=f"Stopped consultation worker target={target}")
    refresh_schedule_doc(base, registry)
    print(f"stopped consult worker {target}")


def cmd_stop(args: argparse.Namespace) -> None:
    base = state_dir(args.state_dir)
    registry = load_registry(base)
    worker = registry.get("workers", {}).get(safe_name(args.name))
    if not worker:
        raise SystemExit(f"unknown worker: {args.name}")
    target = f"{worker['session']}:{worker['window']}"
    if window_exists(worker["session"], worker["window"]):
        tmux("kill-window", "-t", target)
    status_file = worker.get("status_file")
    if status_file:
        write_text(Path(status_file), json.dumps({"state": "stopped", "stopped_at": now_iso()}, ensure_ascii=False) + "\n")
    worker["stopped_at"] = now_iso()
    worker["updated_at"] = now_iso()
    save_registry(base, registry)
    append_manager_log(base, f"stop name={worker['name']} target={target}")
    append_schedule_event(base, "stop", worker=worker["name"], detail=f"Stopped worker target={target}")
    refresh_schedule_doc(base, registry)
    print(f"stopped {worker['name']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="Project-local worker state directory.")
    parser.add_argument("--session", default=DEFAULT_SESSION, help="tmux session name.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create or register the tmux session and state directory.")
    init.add_argument("--cwd", default=os.getcwd(), help="Working directory for the tmux session.")
    init.add_argument("--mission", help="Coordinator mission written into COORDINATOR_SCHEDULE.md.")
    init.set_defaults(func=cmd_init)

    register_coord = sub.add_parser("register-coordinator", help="Register the current/main Codex tmux pane for durable coordinator recovery.")
    register_coord.add_argument("--target", default="auto", help="Coordinator tmux target, as SESSION:WINDOW.PANE. Use auto inside tmux.")
    register_coord.add_argument("--cwd", default=os.getcwd(), help="Working directory for recovered coordinator sessions.")
    register_coord.add_argument("--mission", help="Optional mission update written into COORDINATOR_SCHEDULE.md.")
    register_coord.add_argument("--restart-window-prefix", default="cw-main-recovered", help="Prefix for recovered main coordinator tmux windows.")
    register_coord.add_argument("--allow-missing", action="store_true", help="Allow registering a target not currently present; intended for tests/pre-registration.")
    register_coord.add_argument("--model", help=f"Codex model for recovered coordinators. Default best model: {DEFAULT_WORKER_MODEL}.")
    register_coord.add_argument("--reasoning-effort", help=f"Codex reasoning effort for recovered coordinators. Default best effort: {DEFAULT_WORKER_REASONING}.")
    register_coord.add_argument("--no-best-model", action="store_true", help="Do not apply the manager's default best model/reasoning for recovered coordinators.")
    register_coord.add_argument("--profile")
    register_coord.add_argument("--sandbox", default="danger-full-access")
    register_coord.add_argument("--approval", default="never")
    register_coord.add_argument("--search", action="store_true")
    register_coord.set_defaults(func=cmd_register_coordinator)

    recover_coord = sub.add_parser("recover-coordinator", help="Launch a new main coordinator Codex from durable worker state.")
    recover_coord.add_argument("--old-target", help="Old coordinator tmux target to record and optionally kill.")
    recover_coord.add_argument("--cwd", help="Working directory for the recovered coordinator; defaults to registered coordinator cwd.")
    recover_coord.add_argument("--window", help="Explicit recovered coordinator tmux window name.")
    recover_coord.add_argument("--reason", default="manual-recovery")
    recover_coord.add_argument("--kill-old", action="store_true", help="Kill the old coordinator pane after preparing the recovery prompt.")
    recover_coord.add_argument("--force", action="store_true", help="Replace an existing recovered coordinator window with the same name.")
    recover_coord.add_argument("--startup-wait", type=int, default=8)
    recover_coord.add_argument("--dry-run", action="store_true", help="Write recovery prompt/handoff and print the planned launch without starting Codex.")
    recover_coord.add_argument("--model", help=f"Codex model for the recovered coordinator. Default best model: {DEFAULT_WORKER_MODEL}.")
    recover_coord.add_argument("--reasoning-effort", help=f"Codex reasoning effort. Default best effort: {DEFAULT_WORKER_REASONING}.")
    recover_coord.add_argument("--no-best-model", action="store_true", help="Do not apply default best model/reasoning when no explicit/registered model exists.")
    recover_coord.add_argument("--profile")
    recover_coord.add_argument("--sandbox", default=None)
    recover_coord.add_argument("--approval", default=None)
    recover_coord.add_argument("--search", action="store_true")
    recover_coord.add_argument("--inline-tui", action="store_true", help="Pass Codex --no-alt-screen for inline TUI. Default keeps normal alternate-screen TUI so the bottom prompt/status line stays stable.")
    recover_coord.set_defaults(func=cmd_recover_coordinator)

    launch = sub.add_parser("launch", help="Launch a Codex worker in a new tmux window.")
    launch.add_argument("name", help="Stable worker name.")
    launch.add_argument("--task", help="Worker task text.")
    launch.add_argument("--task-file", help="Path to a worker task prompt.")
    launch.add_argument("--cwd", default=os.getcwd(), help="Working directory for Codex.")
    launch.add_argument("--window", help="Override tmux window name.")
    launch.add_argument("--git-worktree", action="store_true", help="Create an isolated git worktree for this worker.")
    launch.add_argument("--worktree-path", help="Explicit path for --git-worktree.")
    launch.add_argument("--branch", help="Branch name for --git-worktree.")
    launch.add_argument("--base-ref", help="Base ref for --git-worktree, defaults to HEAD.")
    launch.add_argument("--write-scope", action="append", help="Repeatable path/module ownership note.")
    launch.add_argument("--owned-path", action="append", help="Repeatable path owned by this worker; used for conflict checks.")
    launch.add_argument("--resource", action="append", help="Repeatable resource token, such as gpu:0, port:6006, or out:results/run-a.")
    launch.add_argument("--allow-conflict", action="store_true", help="Allow declared owned-path/resource conflicts.")
    launch.add_argument("--notes", help="Coordinator notes to include in the prompt.")
    launch.add_argument("--worker-kind", choices=["standard", "autonomous-experiment", "branch-manager"], default="standard", help="Worker template. autonomous-experiment and branch-manager default to visible interactive mode and supervisor.")
    launch.add_argument("--parent-worker", help="Parent branch-manager worker name for subordinate workers.")
    launch.add_argument("--manager-scope", action="append", help="Repeatable branch-management authority note for branch-manager workers.")
    launch.add_argument("--mode", choices=["exec", "interactive"], help="Defaults to exec for standard workers and interactive for autonomous-experiment/branch-manager workers.")
    launch.add_argument("--startup-wait", type=int, default=8, help="Seconds to wait before pasting the initial prompt into an interactive worker.")
    launch.add_argument("--model", help=f"Codex model for the worker. Default best model: {DEFAULT_WORKER_MODEL}.")
    launch.add_argument("--reasoning-effort", help=f"Codex reasoning effort. Default best effort: {DEFAULT_WORKER_REASONING}.")
    launch.add_argument("--no-best-model", action="store_true", help="Do not apply the manager's default best model/reasoning; use Codex CLI/profile defaults unless explicitly set.")
    launch.add_argument("--profile")
    launch.add_argument("--sandbox", default="danger-full-access")
    launch.add_argument("--approval", default="never")
    launch.add_argument("--search", action="store_true")
    launch.add_argument("--inline-tui", action="store_true", help="Pass Codex --no-alt-screen for inline TUI. Default keeps normal alternate-screen TUI so the bottom prompt/status line stays stable.")
    launch.add_argument("--start-supervisor", action="store_true", help="Start a tmux supervisor window after launch.")
    launch.add_argument("--no-start-supervisor", action="store_true", help="Do not auto-start supervisor for autonomous-experiment workers.")
    launch.add_argument("--supervisor-interval", type=int, default=300)
    launch.add_argument("--supervisor-lines", type=int, default=120)
    launch.add_argument("--supervisor-query-interval", type=int, default=1800)
    launch.add_argument("--supervisor-refresh-schedule-interval", type=int, default=900)
    launch.add_argument("--supervisor-progress-append-interval", type=int, default=1800)
    launch.add_argument("--query-interactive", action="store_true", help="Supervisor queries interactive workers, then sends continue.")
    launch.set_defaults(func=cmd_launch)

    list_cmd = sub.add_parser("list", help="List registered workers and tmux presence.")
    list_cmd.set_defaults(func=cmd_list)

    capture = sub.add_parser("capture", help="Capture recent worker output.")
    capture.add_argument("name")
    capture.add_argument("--lines", type=int, default=120)
    capture.add_argument("--log", action="store_true", help="Read the worker log instead of tmux scrollback.")
    capture.set_defaults(func=cmd_capture)

    send = sub.add_parser("send", help="Send a line of input to an interactive worker.")
    send.add_argument("name")
    send.add_argument("message", nargs="?", default="")
    send.add_argument("--message-file", help="Read message from file.")
    send.add_argument("--escape-first", action="store_true", help="Send Escape before pasting the message.")
    send.add_argument("--escape-after", action="store_true", help="Send Escape shortly after submitting the message.")
    send.add_argument("--via-inbox", action="store_true", help="Write the message into the worker inbox and paste a short read-this-file instruction.")
    send.set_defaults(func=cmd_send)

    interrupt = sub.add_parser("interrupt-send", help="Paste and submit a prompt, then send Escape so Codex switches to it immediately.")
    interrupt.add_argument("name")
    interrupt.add_argument("message", nargs="?", default="")
    interrupt.add_argument("--message-file", help="Read message from file.")
    interrupt.add_argument("--via-inbox", action="store_true", help="Write the message into the worker inbox and paste a short read-this-file instruction.")
    interrupt.set_defaults(func=cmd_interrupt_send)

    peer_send = sub.add_parser("peer-send", help="Write a manager-mediated worker-to-worker message.")
    peer_send.add_argument("source", help="Source worker name.")
    peer_send.add_argument("target", help="Target worker name.")
    peer_send.add_argument("--message", default="", help="Short factual peer message.")
    peer_send.add_argument("--message-file", help="Read peer message from file.")
    peer_send.add_argument("--notify", action="store_true", help="Also paste a short read-inbox notice into the target worker tmux pane.")
    peer_send.add_argument("--escape-first", action="store_true", help="Send Escape before notifying the target worker.")
    peer_send.add_argument("--escape-after", action="store_true", help="Send Escape after notifying the target worker.")
    peer_send.set_defaults(func=cmd_peer_send)

    progress = sub.add_parser("progress", help="Show worker progress files.")
    progress.add_argument("name", nargs="?")
    progress.add_argument("--lines", type=int, default=40)
    progress.set_defaults(func=cmd_progress)

    job_add = sub.add_parser("job-add", help="Register a background job launched by a worker.")
    job_add.add_argument("worker")
    job_add.add_argument("--pid", type=int, required=True)
    job_add.add_argument("--name")
    job_add.add_argument("--kind", default="process")
    job_add.add_argument("--command")
    job_add.add_argument("--log")
    job_add.add_argument("--resource", action="append")
    job_add.set_defaults(func=cmd_job_add)

    jobs = sub.add_parser("jobs", help="List registered background jobs.")
    jobs.add_argument("worker", nargs="?")
    jobs.set_defaults(func=cmd_jobs)

    job_stop = sub.add_parser("job-stop", help="Send a signal to registered background jobs.")
    job_stop.add_argument("worker")
    job_stop.add_argument("--pid", type=int)
    job_stop.add_argument("--name")
    job_stop.add_argument("--all", action="store_true")
    job_stop.add_argument("--signal", default="TERM")
    job_stop.set_defaults(func=cmd_job_stop)

    supervise = sub.add_parser("supervise", help="Run a supervisor loop that captures workers and optionally queries interactive workers.")
    supervise.add_argument("--interval", type=int, default=300)
    supervise.add_argument("--lines", type=int, default=120)
    supervise.add_argument("--once", action="store_true")
    supervise.add_argument("--allow-foreground-loop", action="store_true", help=argparse.SUPPRESS)
    supervise.add_argument("--refresh-schedule-interval", type=int, default=900, help="Seconds between heavy schedule/context refreshes in loop mode.")
    supervise.add_argument("--progress-append-interval", type=int, default=1800, help="Seconds between unchanged supervisor progress entries.")
    supervise.add_argument("--query-interactive", action="store_true")
    supervise.add_argument("--query-any-running", action="store_false", dest="query_only_stalled", help="Allow supervisor queries to running interactive workers, not only stalled ones.")
    supervise.add_argument("--query-escape-first", action="store_true", help="Send Escape before supervisor query prompts.")
    supervise.add_argument("--query-interval", type=int, default=1800)
    supervise.add_argument("--response-wait", type=int, default=45)
    supervise.add_argument("--capture-retention", type=int, default=200)
    supervise.add_argument("--stall-seconds", type=int, default=1800)
    supervise.add_argument("--query-prompt", default="Please briefly report current progress in 1-2 sentences. Do not start new work.")
    supervise.add_argument("--continue-prompt", default="Thanks. Please continue the previous task.")
    supervise.set_defaults(func=cmd_supervise)

    start_supervisor = sub.add_parser("start-supervisor", help="Start the supervisor loop in a tmux window.")
    start_supervisor.add_argument("--cwd", default=os.getcwd())
    start_supervisor.add_argument("--interval", type=int, default=300)
    start_supervisor.add_argument("--lines", type=int, default=120)
    start_supervisor.add_argument("--query-interval", type=int, default=1800)
    start_supervisor.add_argument("--refresh-schedule-interval", type=int, default=900)
    start_supervisor.add_argument("--progress-append-interval", type=int, default=1800)
    start_supervisor.add_argument("--query-interactive", action="store_true")
    start_supervisor.set_defaults(func=cmd_start_supervisor)

    start_health = sub.add_parser("start-health-supervisor", help="Start a tmux health supervisor that recovers Codex panes stuck on known transient errors.")
    start_health.add_argument("--cwd", default=os.getcwd())
    start_health.add_argument("--interval", type=int, default=30)
    start_health.add_argument("--lines", type=int, default=40)
    start_health.add_argument("--stable-seconds", type=int, default=20)
    start_health.add_argument("--cooldown", type=int, default=120)
    start_health.add_argument("--watch-target", action="append", help="Extra interactive Codex target to auto-recover, as NAME=TMUX_TARGET. Use this for the main coordinator pane.")
    start_health.add_argument("--observe-target", action="append", help="Extra target to observe without auto-recovery, as NAME=TMUX_TARGET.")
    start_health.add_argument("--no-workers", action="store_true", help="Do not monitor workers from workers.json.")
    start_health.add_argument("--no-coordinator", action="store_true", help="Do not monitor the registered main coordinator target.")
    start_health.add_argument("--restart-main-on-context-full", action="store_true", help="When the registered coordinator hits context-window exhaustion, launch a recovered coordinator from durable state.")
    start_health.add_argument("--restart-main-when-missing", action="store_true", help="When the registered coordinator target disappears, launch a recovered coordinator from durable state.")
    start_health.add_argument("--keep-old-main", action="store_true", help="Do not kill the old coordinator pane when auto-recovering the main coordinator.")
    start_health.add_argument("--dry-run", action="store_true", help="Detect and log recovery actions without sending prompts.")
    start_health.add_argument("--escape-after", action="store_true", help="Send Escape after submitting a recovery prompt.")
    start_health.add_argument("--recovery-prompt", help="Override the default recovery prompt sent to stuck interactive Codex panes.")
    start_health.add_argument("--force", action="store_true", help="Replace an existing cw-health-supervisor window.")
    start_health.set_defaults(func=cmd_start_health_supervisor)

    stop_health = sub.add_parser("stop-health-supervisor", help="Kill the tmux health supervisor window.")
    stop_health.add_argument("--window", default="cw-health-supervisor")
    stop_health.set_defaults(func=cmd_stop_health_supervisor)

    resume = sub.add_parser("resume", help="Resume an interrupted worker from its durable artifacts.")
    resume.add_argument("name")
    resume.add_argument("--session")
    resume.add_argument("--window")
    resume.add_argument("--mode", choices=["exec", "interactive"])
    resume.add_argument("--force", action="store_true")
    resume.add_argument("--startup-wait", type=int, default=8)
    resume.add_argument("--model", help=f"Codex model for the resumed worker. Default best model: {DEFAULT_WORKER_MODEL}.")
    resume.add_argument("--reasoning-effort", help=f"Codex reasoning effort. Default best effort: {DEFAULT_WORKER_REASONING}.")
    resume.add_argument("--no-best-model", action="store_true", help="Do not apply the manager's default best model/reasoning; use Codex CLI/profile defaults unless explicitly set.")
    resume.add_argument("--profile")
    resume.add_argument("--sandbox", default="danger-full-access")
    resume.add_argument("--approval", default="never")
    resume.add_argument("--search", action="store_true")
    resume.add_argument("--inline-tui", action="store_true", help="Pass Codex --no-alt-screen for inline TUI when resuming. Default keeps normal alternate-screen TUI.")
    resume.set_defaults(func=cmd_resume)

    collect = sub.add_parser("collect", help="Write a coordinator summary from workers, jobs, reports, and git diffs.")
    collect.add_argument("--lines", type=int, default=40)
    collect.add_argument("--output")
    collect.set_defaults(func=cmd_collect)

    compact_memory = sub.add_parser("compact-memory", help="Refresh compact coordinator memory and context pack.")
    compact_memory.add_argument("--reason", default="manual")
    compact_memory.add_argument("--note", help="Short coordinator memory note to preserve outside chat history.")
    compact_memory.add_argument("--decision", help="Short decision to preserve outside chat history.")
    compact_memory.add_argument("--next-action", help="Short next checkpoint/action to preserve outside chat history.")
    compact_memory.add_argument("--mission", help="Optional mission update before compacting memory.")
    compact_memory.add_argument("--print", action="store_true", help="Print the refreshed memory file.")
    compact_memory.add_argument("--context-pack", action="store_true", help="With --print, print the shorter context pack instead of full compact memory.")
    compact_memory.set_defaults(func=cmd_compact_memory)

    constraints = sub.add_parser("constraints", help="View or update unified coordinator constraints loaded by all launched Codex processes.")
    constraints.add_argument("--print", action="store_true", help="Print the constraints file after any update.")
    constraints.add_argument("--append", action="append", help="Append one coordinator-wide constraint bullet. Repeatable.")
    constraints.add_argument("--set-file", help="Replace constraints with the contents of this Markdown file.")
    constraints.add_argument("--reset-defaults", action="store_true", help="Reset constraints to the built-in defaults.")
    constraints.add_argument("--tensorboard-port-range", help="Append/update a TensorBoard/dashboard safe port range constraint, for example 16006-16099.")
    constraints.set_defaults(func=cmd_constraints)

    schedule = sub.add_parser("schedule", help="Refresh and show the coordinator scheduling document path.")
    schedule.add_argument("--print", action="store_true", help="Print the scheduling document content.")
    schedule.set_defaults(func=cmd_schedule)

    schedule_note = sub.add_parser("schedule-note", help="Append a coordinator scheduling note, decision, or next action.")
    schedule_note.add_argument("--event", default="coordinator-note")
    schedule_note.add_argument("--worker")
    schedule_note.add_argument("--note")
    schedule_note.add_argument("--decision")
    schedule_note.add_argument("--next-action")
    schedule_note.add_argument("--mission", help="Update the current overall mission.")
    schedule_note.set_defaults(func=cmd_schedule_note)

    start_consult = sub.add_parser("start-consult", help="Start a read-only user consultation Codex worker in a tmux window.")
    start_consult.add_argument("--cwd", default=os.getcwd(), help="Working directory for the consultation Codex process.")
    start_consult.add_argument("--window", default="cw-consult", help="tmux window name for the consultation worker.")
    start_consult.add_argument("--startup-wait", type=int, default=8, help="Seconds to wait before pasting the consultation prompt.")
    start_consult.add_argument("--force", action="store_true", help="Replace an existing consultation window with the same name.")
    start_consult.add_argument("--model", help=f"Codex model for the consultation worker. Default best model: {DEFAULT_WORKER_MODEL}.")
    start_consult.add_argument("--reasoning-effort", help=f"Codex reasoning effort. Default best effort: {DEFAULT_WORKER_REASONING}.")
    start_consult.add_argument("--no-best-model", action="store_true", help="Do not apply the manager's default best model/reasoning; use Codex CLI/profile defaults unless explicitly set.")
    start_consult.add_argument("--profile")
    start_consult.add_argument("--sandbox", default="read-only")
    start_consult.add_argument("--approval", default="never")
    start_consult.add_argument("--search", action="store_true")
    start_consult.add_argument("--inline-tui", action="store_true", help="Pass Codex --no-alt-screen for inline TUI. Default keeps normal alternate-screen TUI.")
    start_consult.set_defaults(func=cmd_start_consult)

    consult_sync = sub.add_parser("consult-sync", help="Refresh consultation context and optionally notify the consultation worker.")
    consult_sync.add_argument("--message", help="Coordinator note to include in the sync event and notification.")
    consult_sync.add_argument("--notify", action="store_true", help="Paste a refresh notice into the consultation worker window.")
    consult_sync.add_argument("--escape-first", action="store_true", help="Send Escape before the notification prompt.")
    consult_sync.set_defaults(func=cmd_consult_sync)

    consult_context = sub.add_parser("consult-context", help="Refresh and show the consultation context path.")
    consult_context.add_argument("--print", action="store_true", help="Print the consultation context content.")
    consult_context.set_defaults(func=cmd_consult_context)

    stop_consult = sub.add_parser("stop-consult", help="Kill the consultation worker tmux window.")
    stop_consult.add_argument("--window", help="Fallback window name if registry is incomplete.")
    stop_consult.set_defaults(func=cmd_stop_consult)

    stop = sub.add_parser("stop", help="Kill one worker tmux window.")
    stop.add_argument("name")
    stop.set_defaults(func=cmd_stop)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
