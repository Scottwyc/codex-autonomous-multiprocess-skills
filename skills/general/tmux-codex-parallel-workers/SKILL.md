---
name: tmux-codex-parallel-workers
description: Launch, supervise, health-monitor, and stop independent Codex CLI worker processes in separate tmux sessions for parallel branch tasks. Use when the user wants tmux-managed Codex workers, background Codex sessions, multi-session autonomous workers, visible autonomous experiment workers, subordinate branch-manager workers, manager-mediated worker-to-worker communication, unified coordinator constraints, compact coordinator memory/context packs, a read-only user consultation worker, auto-recovery from Codex tmux pane errors, or when long-running autonomous project management defaults to Codex worker parallelism.
---

# Tmux Codex Parallel Workers

## Overview

Use this skill to run separate Codex CLI processes in tmux sessions while the current agent remains the coordinator. It is for parallelism in long-running autonomous work: independent code exploration, experiment monitoring, report drafting, log analysis, user consultation, or bounded code changes with disjoint ownership.

Prefer built-in subagents when structured result return is enough and persistence is unnecessary. Prefer this tmux mode when long-running autonomous project management is active by default, or when the user wants real OS-level Codex processes, persistent tmux sessions, manual attach/capture, user consultation, or long-running background workers.

For substantial experimental branches, the coordinator may launch a subordinate `branch-manager` worker. The main coordinator gives that worker the branch goal and resource envelope, then the branch manager launches and coordinates front-line `autonomous-experiment` child workers. This reduces main-context load while preserving coordinator-owned final review and user-facing decisions.

This skill follows the same operational principle as the Qwen long-mode tooling: reliable paste-buffer prompt delivery, durable progress files, explicit work plans/reports, and supervisor prompts that return the worker to its previous task after a status check.

It also includes a Qwen-style health supervisor for Codex tmux panes. The normal supervisor captures progress and optional worker status; the health supervisor watches pane tails for recoverable Codex transport/subprocess errors and sends a bounded recovery prompt after a stability/cooldown check.

## Core Rules

1. Keep the main agent as coordinator.
   - The coordinator owns task decomposition, resource planning, final review, integration, and user-facing conclusions.
   - Workers are branch executors, not final decision makers.
2. Give every worker a bounded task and write scope.
   - Avoid overlapping file ownership.
   - If two workers must inspect the same area, make at least one read-only.
   - Use `--owned-path` for paths that the manager should conflict-check.
3. Record state in the project.
   - Use a project-local state directory, default `.codex/tmux-workers/`.
   - Keep worker prompts, logs, work plans, progress files, reports, inbox messages, status files, captures, and the bounded current-state `workers.json` registry there.
   - Keep `.codex/tmux-workers/COORDINATOR_CONSTRAINTS.md` current as the coordinator-wide constraint contract. It is a bounded live view, not an append-only policy history: remove superseded main targets, resource owners, authorizations, and overrides from the live file, while preserving old versions under `archive/constraints/`. All launched workers, branch managers, consultation workers, resumed workers, and recovered coordinators must read it before their task-specific prompt.
   - Keep `.codex/tmux-workers/COORDINATOR_SCHEDULE.md` current so the user can audit the coordinator's worker plan, scheduling decisions, current results, and next actions.
   - The manager mounts the state directory into worker Codex processes with `--add-dir` when using `workspace-write`; this is required when a worker runs in an isolated git worktree and the state directory is outside its `--cd` root.
   - Workers may update their progress/report and use manager commands such as `job-add`, but should not manually edit `workers.json`, status files, schedule files, or registry files.
   - Treat `COORDINATOR_SCHEDULE.md`, `COORDINATOR_MEMORY.md`, `COORDINATOR_CONTEXT_PACK.md`, `COORDINATOR_RECOVERY.md`, and `consult/CONSULT_CONTEXT.md` as bounded current-state views, not historical logs. They should list current workers, a small recent-terminal summary, current decisions, and evidence pointers only.
   - Render each active worker once in a concise current-state table. Do not append repeated per-worker task, key-file, resource, progress, report, job, status, log, or capture blocks to schedule/consult/memory/recovery views; obtain those details on demand through `list`, `progress`, `jobs`, the registry, and exact evidence paths.
   - When the live constraints accumulate superseded or conflicting rules, write a concise current replacement and apply it with `constraints --set-file <file>`. The manager archives the exact previous live version before replacement. Use `constraints --append` only for still-current additions, not as a permanent history mechanism.
   - Preserve full history in `schedule_events.jsonl`, `coordinator_memory_events.jsonl`, worker progress/report files, and timestamped archives. Rotate bounded event files and archive oversized current-state files before regenerating them.
   - Treat `workers.json` as a bounded current-state registry, not the complete historical ledger. Keep every non-terminal worker plus only a small recent terminal tail; archive a full timestamped registry snapshot before pruning. Use `compact-registry` when auditing or repairing an older state directory.
   - Keep manager/supervisor logs bounded and rotated. Recovery handoff must never enumerate every historical stopped worker or every historical worker's key-file block.
   - Treat `logs/*.log` created by interactive `tmux pipe-pane` capture as replaceable TUI transcripts, not experiment evidence. The supervisor bounds closed terminal/orphan transcripts above about 5 MB and safely rebinds active registered transcripts above about 50 MB; `compact-tui-logs` provides a dry-run-first manual audit. Never apply this lifecycle to worker-owned experiment/job logs outside the state directory.
4. Do not launch workers into shared resources blindly.
   - Record project-wide resource rules in `COORDINATOR_CONSTRAINTS.md` before launching workers.
   - Use `constraints --append ...` or `constraints --tensorboard-port-range 16006-16099` to set shared requirements such as TensorBoard safe ports, dashboard bind hosts, output roots, SSH/remote-job rules, or cleanup limits.
   - Assign GPU IDs, ports, output directories, checkpoint paths, and result folders explicitly.
   - Use `--resource` tokens such as `gpu:0`, `port:6006`, or `out:results/run-a` so the manager can catch exact conflicts.
   - TensorBoard/dashboard workers should bind to `127.0.0.1` by default, use a coordinator-approved port, and register that port as a resource or job/progress entry.
5. Capture and review worker output before acting on it.
   - Treat worker changes as untrusted until inspected by the coordinator.
   - For experiment execution that the user should be able to watch, use `--worker-kind autonomous-experiment` or `--mode interactive`; this opens a visible Codex worker session in tmux with the normal alternate-screen TUI so the bottom prompt/status line remains stable.
   - `exec` workers are suitable for bounded one-shot tasks, but they are not the preferred mode when the user wants to observe the worker's reasoning and command sequence live.
6. Protect the coordinator context budget.
   - Worker-facing chat, progress files, report excerpts, supervisor captures, and consultation answers must be concise by default.
   - Do not paste raw logs, full diffs, long tables, or complete tmux transcripts into coordinator-facing updates. Write long evidence to files and provide paths plus a 5-10 line summary.
   - Coordinator spot checks should start with `compact-memory --print --context-pack`, `compact-memory --print`, `list`, `jobs`, and `progress --lines 20`; use `schedule`, `collect --lines 20/30`, and short `capture --lines 80` only when summaries are insufficient.
   - Keep `COORDINATOR_CONTEXT_PACK.md` and `COORDINATOR_MEMORY.md` as the coordinator's short reload memory, `COORDINATOR_SCHEDULE.md` as the audit/control document, and `CONSULT_CONTEXT.md` as the user-consultation summary. None of them should become raw scrollback mirrors.
   - After important decisions, run `compact-memory --note ... --decision ... --next-action ...` so the next checkpoint or recovered coordinator can continue from files rather than chat history.
   - Do not call `compact-memory`, `schedule-note`, `consult-sync`, or schedule refresh merely to record that nothing changed. Repeated unchanged notes are noise and must be coalesced.
   - Do not render every historical stopped worker, full task text, progress/report tail, or repeated repository-wide Git diff into the schedule or consultation context. Use the registry and artifact paths for deep audit.
   - When `workers.json` grows beyond roughly 1 MB or 128 records, compact it immediately. The manager automatically applies this guardrail on registry writes; do not disable it to preserve history because full snapshots belong under `archive/registry/`.
   - Keep only one supervisor per state directory. Duplicate supervisors create duplicate captures, schedule refreshes, and progress entries.
   - Default supervisor cadence should be conservative: about 15 minutes for ordinary captures, about 1 hour for heavy schedule/context refreshes, and about 2 hours for unchanged progress heartbeats. Tighten temporarily only for launch stabilization or failure diagnosis.
7. Use independent tmux sessions by default.
   - `--session cw` is a namespace/prefix, not a shared session, unless `--shared-session` is explicitly passed.
   - A worker named `exp-a` should normally run at `cw-exp-a:codex`; consultation runs at `cw-consult:consult`; supervisors run at `cw-supervisor:supervisor` and `cw-health-supervisor:health-supervisor`.
   - This lets the user attach to multiple workers from different terminals without tmux active-window switching.
   - Use `tmux ls | rg '^cw(-|:)'` to list managed sessions and `tmux attach -t cw-exp-a` to inspect one worker.
8. Use paste-buffer for prompt delivery.
   - Multiline instructions must go through the manager's `send` or `interrupt-send`, which uses `tmux set-buffer`, `tmux paste-buffer`, then Enter.
   - If the worker is busy and a new instruction must interrupt it, use `interrupt-send`, which submits the new message first and then sends Escape so Codex switches to the queued instruction immediately.
9. Use the strongest available worker model by default.
   - The manager defaults tmux Codex workers to `gpt-5.5` with `model_reasoning_effort="xhigh"` in this environment.
   - Override globally with `CODEX_WORKER_DEFAULT_MODEL` and `CODEX_WORKER_DEFAULT_REASONING`.
   - Override per worker with `--model` and `--reasoning-effort`, or use `--no-best-model` to fall back to Codex CLI/profile defaults.
10. Offer a dedicated user consultation window when the user wants to inspect details without interrupting the coordinator.
   - Start it with `start-consult`; it runs a read-only Codex process in `cw-consult:consult`.
   - It reads `.codex/tmux-workers/consult/CONSULT_CONTEXT.md` and `COORDINATOR_SCHEDULE.md` before answering.
   - It must answer questions only; execution, recovery, launch, stop, and integration decisions stay with the coordinator or manager commands.
11. Use the health supervisor for long-lived autonomous runs.
   - Start it with `start-health-supervisor`; it runs in `cw-health-supervisor:health-supervisor`.
   - It automatically monitors registered interactive workers and the registered main coordinator target unless disabled.
   - Its active set and loop-state cache must contain only the current registered coordinator, non-terminal registered workers, and explicit watch/observe targets. Prune normally closed, stopped, or deregistered targets each cycle; keep a currently registered but temporarily missing target so recovery remains possible.
   - When the main coordinator itself runs inside tmux, register it with `register-coordinator` before starting health supervision. This writes `COORDINATOR_RECOVERY.md`, records the coordinator target/model/cwd, and lets a later coordinator reconstruct the run from durable state.
   - `register-coordinator` locks the registered window name with tmux `automatic-rename=off` and `allow-rename=off`. Managed worker windows receive the same lock at creation, and in-place recovery reapplies it before and after respawn. Do not re-enable automatic/application window renaming: a renamed window can make the same live pane look like a missing coordinator or worker target.
   - If the registered coordinator hits context-window exhaustion, start health supervision with `--restart-main-on-context-full`. `recover-coordinator` then uses `tmux respawn-pane -k` at the exact registered target, preserving the registry target and preventing a second main coordinator session.
   - Treat `--restart-main-when-missing` as an explicit opt-in only when coordinator-wide constraints authorize automatic replacement of a missing target. Missing-target recovery passes `--new-target`; the manager fails closed without it and rejects it while the registered target is still present. `--keep-old-main` is retained only as a deprecated no-op.
   - It auto-recovers only interactive Codex panes. Use `--observe-target` for panes that should be logged but never receive pasted recovery prompts.
   - It is for recoverable network/subprocess stalls, not semantic experiment failures, quota/auth failures, merge conflicts, or metric regressions.
12. Use branch-manager workers for major branches.
   - Start them with `launch <name> --worker-kind branch-manager`; they default to visible interactive mode and supervisor like autonomous experiment workers.
   - Give each branch manager a clear mission, `--manager-scope`, resource envelope, and allowed write/output roots.
   - Branch managers may launch child workers with `--parent-worker <branch-manager>` and coordinate their reports, jobs, and peer messages.
   - Branch managers must remain responsive between events. They must not use manager-owned long `sleep`, blocking terminal waits, or dense polling loops as a scheduler; record the next gate, return idle, and rely on peer completion/failure messages plus the normal supervisor.
   - Require child workers to use `peer-send ... --notify` for terminal completion, accepted handoff, failure, unexpected termination, and resource-release events. Keep routine health/checkpoint messages non-notifying so event-driven coordination does not become prompt spam.
   - Branch managers produce branch-level progress/report summaries for the main coordinator; they do not own final merge, promotion, cross-branch resource decisions, or user-facing conclusions unless explicitly delegated.
13. Allow front-line worker communication only through manager-mediated messages.
   - Use `peer-send <source> <target> --message ...` or `--message-file ...`.
   - Use `peer-send <source> main-coordinator --message ... --notify` for terminal completion, failure, accepted handoff, or resource-release evidence. `main`, `main-coordinator`, and `coordinator` resolve only to the registered authoritative coordinator target; the manager writes a concise coordinator inbox message and never creates a replacement main target through this path.
   - Peer messages are for short factual evidence, blockers, dependency notices, and artifact paths.
   - Peer messages must not silently change another worker's scope, resources, experiment gate, or final decision authority.
14. Keep watcher and supervisor lifecycle bounded.
   - For periodic monitor/watch workers, keep only the watcher for the current decision gate. Stop and mark the superseded watcher before keeping or launching the next one.
   - Prefer peer events and the normal supervisor over branch-manager wait loops. Create a dedicated bounded watcher only when a concrete future gate has no event route; it must own no experiment work and must close immediately after reporting that gate.
   - Use one supervisor per state directory. Before starting another, inspect tmux and the supervisor status file and remove or stop duplicates.
   - Use event-driven coordinator updates. Stable unchanged observations should widen the next check interval and should not create schedule, compact-memory, consultation, or progress entries.
   - Keep the two supervisor roles distinct. The ordinary worker supervisor captures progress and automatically backs off after unchanged cycles; the health supervisor detects recoverable pane failures/context exhaustion and therefore keeps a fixed conservative cadence while pruning stopped targets.
   - Use `--dashboard` when the user should be able to inspect current supervisor progress directly in tmux. The dashboard is an ephemeral single-screen view; it must not tighten polling cadence or append unchanged dashboard text to logs or project-management documents.

## Manager Script

Use `scripts/codex_tmux_manager.py` for deterministic orchestration.

For a detailed Chinese architecture and usage guide, read [references/codex-tmux-framework.zh.md](references/codex-tmux-framework.zh.md).

Initialize a session:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  init --cwd "$PWD" --mission "Run the long autonomous evaluation line and coordinate worker reports."
```

Start a read-only user consultation window:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-consult --cwd "$PWD"
```

Register a tmux-hosted main coordinator for durable restart:

```bash
tmux display-message -p '#S:#W.#{pane_index}'
python /home/wuyangcheng/.codex/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py \
  --state-dir .codex/tmux-workers \
  --session cw \
  register-coordinator \
  --target <SESSION:WINDOW.PANE> \
  --cwd "$PWD" \
  --mission "Run the long autonomous evaluation line and coordinate worker reports."
```

Manual coordinator recovery from durable state:

```bash
python /home/wuyangcheng/.codex/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py \
  --state-dir .codex/tmux-workers \
  --session cw \
  recover-coordinator \
  --reason manual-restart
```

The default recovery policy is stable-target in-place replacement. It writes the durable handoff, respawns the exact registered pane, and leaves the coordinator registry target unchanged. If the registered target is confirmed absent, recovery fails closed unless the coordinator explicitly adds `--new-target`. The manager rejects `--new-target` while the registered target is still present.

```bash
python /home/wuyangcheng/.codex/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py \
  --state-dir .codex/tmux-workers \
  --session cw \
  recover-coordinator \
  --reason confirmed-target-missing \
  --new-target
```

Refresh the consultation context and notify the consultation window:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  consult-sync --notify --message "Coordinator checkpoint refreshed after reviewing worker reports."
```

Launch a one-shot unattended worker:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch result-audit \
  --cwd "$PWD" \
  --mode exec \
  --write-scope "read-only: results/, logs/, docs/" \
  --owned-path "docs/result-audit.md" \
  --resource "cpu:analysis" \
  --task "Inspect current experiment outputs and report the newest complete metrics, missing files, and recommended next evaluation."
```

Launch an interactive worker when follow-up input may be needed:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  launch report-draft \
  --mode interactive \
  --write-scope "docs/autonomous_followup.md" \
  --task-file /tmp/report_worker_prompt.md
```

Launch a long-running interactive worker with an attached supervisor:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch long-eval \
  --cwd "$PWD" \
  --git-worktree \
  --mode interactive \
  --write-scope "results/long-eval/, docs/long-eval-report.md" \
  --owned-path "results/long-eval/" \
  --owned-path "docs/long-eval-report.md" \
  --resource "gpu:0" \
  --task "Run the evaluation plan, keep progress updated, and write a final report." \
  --start-supervisor \
  --supervisor-interval 900 \
  --query-interactive
```

Launch a visible autonomous experiment worker:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch exp-a \
  --cwd "$PWD" \
  --git-worktree \
  --worker-kind autonomous-experiment \
  --owned-path "results/exp-a/" \
  --owned-path "configs/exp_a.yaml" \
  --resource "gpu:1" \
  --resource "out:results/exp-a" \
  --write-scope "Run and iterate experiment A; keep actions visible in the tmux Codex pane; register background jobs; update progress/report." \
  --task-file /tmp/exp_a_autonomous_worker.md
```

`autonomous-experiment` defaults to `interactive` mode and starts the supervisor unless `--no-start-supervisor` is passed. Attach with `tmux attach -t cw-exp-a` to watch the Codex worker's actual planning, inspections, commands, and recovery decisions.

Launch a subordinate branch manager for a major experiment line:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch moe-branch-manager \
  --cwd "$PWD" \
  --worker-kind branch-manager \
  --manager-scope "Coordinate MoE ablation branch; may launch child workers under results/moe-branch/ and docs/moe-branch/" \
  --owned-path "results/moe-branch/" \
  --owned-path "docs/moe-branch/" \
  --resource "gpu:0-1" \
  --write-scope "Plan and coordinate child autonomous-experiment workers for the MoE branch; summarize branch results for the main coordinator." \
  --task "Create a branch plan, launch bounded child experiment workers with --parent-worker moe-branch-manager, coordinate peer messages, and maintain a branch-level report."
```

A branch manager can then launch child workers with `--parent-worker`:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch moe-k-sweep \
  --parent-worker moe-branch-manager \
  --worker-kind autonomous-experiment \
  --owned-path "results/moe-branch/k-sweep/" \
  --resource "gpu:0" \
  --task "Run the K sweep child experiment, update progress, and report metrics/artifacts to the branch manager."
```

Send a manager-mediated worker-to-worker message:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  peer-send moe-k-sweep moe-router-audit \
  --message "K sweep produced config path results/moe-branch/k-sweep/best_config.yaml; please use it for the router audit." \
  --notify
```

Inspect workers:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers constraints --print
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers constraints --tensorboard-port-range 16006-16099
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers compact-memory --print --context-pack
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers compact-memory --note "Current branch manager reports are stable." --decision "Wait for running jobs before launching more workers." --next-action "Check jobs and compact memory at the next checkpoint."
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers compact-registry
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers compact-tui-logs
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers compact-tui-logs --include-active --apply
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers list
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers schedule
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers consult-context --print
cat .codex/tmux-workers/peer_messages.jsonl
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers capture result-audit --lines 80 --log
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers progress result-audit
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers jobs
tmux ls | rg '^cw(-|:)'
tmux attach -t cw-result-audit
```

Use larger `--lines` values only when diagnosing a concrete failure. Normal coordination should read schedule/progress/report summaries first and keep long captures on disk.

Send input or stop a worker:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers send report-draft "Continue with the summary section only."
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers send report-draft --via-inbox --message-file /tmp/long_instruction.md
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers interrupt-send report-draft "Pause current work, save progress, and report current state."
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers stop report-draft
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers stop-consult
```

Run or start the supervisor loop:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers supervise --once
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers start-supervisor --interval 900 --dashboard
```

Start health monitoring and transient-error recovery:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor --interval 30 --stable-seconds 20 --cooldown 120 --dashboard
```

Include the main coordinator pane when the coordinator itself is running inside tmux:

```bash
tmux display-message -p '#S:#W.#{pane_index}'
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  register-coordinator --target <SESSION:WINDOW.PANE> --cwd "$PWD"

python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor --restart-main-on-context-full
```

Add `--restart-main-when-missing` only when coordinator-wide constraints explicitly authorize automatic replacement of a missing target. Context-full recovery stays at the exact registered target; missing-target replacement is the only health-supervisor path that requests `--new-target`. Use `--dry-run` first if you want detection logs without pasted recovery prompts. Stop it with:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers --session cw stop-health-supervisor
```

Register or stop background jobs launched by a worker:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers job-add long-eval --pid 12345 --name train-main --log /path/to/train.log --resource gpu:0 --command "nohup python train.py > train.log 2>&1 &"
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers job-stop long-eval --name train-main --signal TERM
```

Resume or collect worker state:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers resume long-eval --mode interactive
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers collect --lines 30
```

Record coordinator scheduling decisions:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers schedule-note --event decision --worker long-eval --decision "Keep long-eval running; metrics not ready." --next-action "Check jobs and progress after the next supervisor capture."
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers schedule --print
```

## Launch Defaults

The script defaults to:

- tmux namespace: `cw`
- tmux topology: independent sessions by default, so worker `exp-a` runs at `cw-exp-a:codex`, consult at `cw-consult:consult`, supervisor at `cw-supervisor:supervisor`, and health supervisor at `cw-health-supervisor:health-supervisor`
- legacy shared-session topology: pass global `--shared-session` before the subcommand to use one `cw` session with multiple windows such as `cw:cw-exp-a`
- state directory: `.codex/tmux-workers`
- worker mode: `codex exec`
- autonomous experiment worker mode: normal Codex alternate-screen interactive TUI in tmux, preserving the bottom prompt/status line
- optional inline TUI: pass `--inline-tui` only when preserving terminal scrollback is more important than a stable bottom prompt/status line; this forwards Codex `--no-alt-screen`
- worker model: `gpt-5.5`
- worker reasoning effort: `xhigh`
- sandbox: `danger-full-access` for launched/resumed workers in this environment, because the user authorized YOLO-style autonomous experiment workers and `workspace-write` caused read-only `.codex` mounts plus blocked SSH for visible experiment branches
- extra writable directory for execution workers: `.codex/tmux-workers` via Codex CLI `--add-dir` when `workspace-write` is explicitly selected
- approval policy: `never`
- progress files: `.codex/tmux-workers/progress/<worker>.md`
- reports: `.codex/tmux-workers/reports/<worker>.md`
- work plans: `.codex/tmux-workers/workplans/<worker>.md`
- inbox directories: `.codex/tmux-workers/inbox/<worker>/`
- status files: `.codex/tmux-workers/status/<worker>.json`
- job registries: `.codex/tmux-workers/jobs/<worker>.json`
- optional git worktrees: `.codex/tmux-workers/git-worktrees/<worker>/`
- coordinator schedule: `.codex/tmux-workers/COORDINATOR_SCHEDULE.md`
- coordinator constraints: `.codex/tmux-workers/COORDINATOR_CONSTRAINTS.md`
- consultation context: `.codex/tmux-workers/consult/CONSULT_CONTEXT.md`
- consultation prompt: `.codex/tmux-workers/consult/consult.prompt.md`
- normal supervisor session/status/log: `cw-supervisor:supervisor`, `.codex/tmux-workers/status/supervisor.json`, `.codex/tmux-workers/logs/supervisor.log`
- health supervisor session/status/log: `cw-health-supervisor:health-supervisor`, `.codex/tmux-workers/status/health_supervisor.json`, `.codex/tmux-workers/logs/health-supervisor.log`
- consultation log/status: `.codex/tmux-workers/logs/consult.log` and `.codex/tmux-workers/consult/consult.status.json`

Override these when project policy requires it:

- Use `--sandbox danger-full-access` as the default for project-local autonomous experiment workers in this environment. Keep worker prompts conservative around destructive operations such as `rm`, and prefer manager `stop`/`stop-consult`/`stop-health-supervisor` commands for worker cleanup.
- Use `--sandbox workspace-write` only for intentionally restricted read/write workers that do not need SSH or cross-mount writes.
- Prefer placing all visible worker tmux sessions on the local/nature machine under the project-local `cw-` prefix. When a branch needs another server, the local worker should SSH outward to zhuhai/zeng/etc. rather than creating or depending on remote Codex worker panes. This keeps monitoring and cleanup centralized for the user.
- Use `--model`, `--reasoning-effort`, `--profile`, or `--no-best-model` when cost, quota, speed, or model availability requires a different Codex configuration.
- Use `--search` only when live web access is needed.
- Use `--allow-conflict` only when a path or resource overlap is intentional and externally safe.
- Use `--git-worktree` for concurrent code edits that should not happen in the shared working tree.

Best-model defaults can be changed for the current shell:

```bash
export CODEX_WORKER_DEFAULT_MODEL=gpt-5.5
export CODEX_WORKER_DEFAULT_REASONING=xhigh
```

## Worker Task Pattern

Before launching, decide:

- what the worker can finish independently
- what files or resources it owns
- what evidence it must return
- what it must avoid touching
- whether it is allowed to edit or only inspect
- whether long instructions should be sent by inbox instead of direct paste

For detailed prompt structure, read [references/worker-protocol.md](references/worker-protocol.md).

## Autonomous Experiment Worker Pattern

Use `--worker-kind autonomous-experiment` for a worker that should independently run an experiment branch while the coordinator keeps overall control.

This worker type is designed for visibility:

- it defaults to `interactive`, so the worker's Codex session is visible in its dedicated tmux session
- it defaults to the normal Codex alternate-screen TUI so the bottom prompt/status line and `working` indicator stay visible
- `--inline-tui` is opt-in and may make the bottom prompt/status line less reliable
- it prints a tmux attach hint after launch
- its prompt tells the worker to briefly state major intent before important actions
- long training/evaluation commands should be background jobs registered with `job-add`, while the visible Codex pane remains the place where the worker inspects logs, diagnoses failures, explains decisions, and updates progress/report

The coordinator should still assign explicit `--owned-path` and `--resource` tokens. The worker may iterate inside its assigned experiment branch, but final acceptance, merging, promotion, and user-facing conclusions remain coordinator-owned.

If an older worker was launched with inline TUI and its bottom prompt/status line is missing, restart it through durable state: `stop <worker>` then `resume <worker> --mode interactive` without `--inline-tui`. For the main coordinator pane, prefer `recover-coordinator` after `register-coordinator` has written a durable handoff; it respawns the exact registered pane in place.

## Coordinator Schedule Pattern

The coordinator must maintain `.codex/tmux-workers/COORDINATOR_SCHEDULE.md` as the user-auditable control document.

The manager refreshes this document after major operations: `init`, `launch`, `send`, `job-add`, `job-stop`, `supervise`, `start-supervisor`, `resume`, `collect`, and `stop`.

The manager also refreshes `.codex/tmux-workers/consult/CONSULT_CONTEXT.md` whenever it refreshes the schedule. This context is optimized for the dedicated user consultation worker and includes the mission, worker overview, key file paths, recent scheduling events, and a schedule excerpt.

The manager also maintains two smaller coordinator-memory files:

- `.codex/tmux-workers/COORDINATOR_CONTEXT_PACK.md`: the shortest reload packet for the main coordinator.
- `.codex/tmux-workers/COORDINATOR_MEMORY.md`: compact working memory with active workers, latest summaries, recent decisions, peer messages, resources, and evidence paths.

Use `compact-memory --print --context-pack` before routine checkpoints, and use `compact-memory --note ... --decision ... --next-action ...` after meaningful coordination decisions. This is the main mechanism for reducing pressure on the model context window.

The manager also maintains `.codex/tmux-workers/COORDINATOR_CONSTRAINTS.md`. This is the bounded live operating contract for all launched Codex processes. Update it before launching workers when the project has global requirements such as TensorBoard safe port ranges, GPU/resource ownership, output roots, dashboard bind hosts, SSH/remote-job conventions, cleanup limits, or dataset/checkpoint immutability rules. When old rules are superseded, replace the live contract with `constraints --set-file <concise-current.md>`; the manager archives the previous exact version under `archive/constraints/`.

Use `schedule-note` whenever the coordinator makes a non-obvious decision, such as launching a worker, changing resource allocation, accepting a result, deferring a merge, recovering a stalled worker, or stopping a job. The document should let a user answer:

- which workers exist and why they were launched
- what each worker owns and what resources it uses
- what each worker has produced so far
- what jobs are still running
- what the coordinator decided and what the next checkpoint is
- where to inspect evidence, logs, reports, and git diffs

## User Consultation Window Pattern

Use `start-consult` when the user wants a persistent question-and-answer window that does not interrupt the main coordinator. The consultation worker is a separate interactive Codex process in tmux, normally at `cw-consult:consult`, launched with the same strongest-model default as other workers but with `--sandbox read-only`.

The consultation worker is not an execution worker. It should:

- read `consult/CONSULT_CONTEXT.md` and `COORDINATOR_SCHEDULE.md` before answering
- answer user questions about worker state, logs, reports, resources, blockers, and evidence paths
- state missing evidence instead of guessing
- refuse to mutate project state and redirect execution requests back to the coordinator or manager commands

Refresh its context after important coordinator decisions:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" --state-dir .codex/tmux-workers consult-sync --notify --message "New checkpoint recorded."
```

## Communication Pattern

Use direct `send` for short interactive instructions. Use `--via-inbox` for longer or important instructions:

1. The manager writes the full message to `.codex/tmux-workers/inbox/<worker>/<timestamp>.md`.
2. The manager pastes a short instruction telling the worker to read that file.
3. The worker records the action in its progress file.

This keeps the command auditable and avoids losing long prompts in terminal scrollback.

## Supervisor Pattern

Use the supervisor for long-running interactive workers or for periodic capture of all worker windows.

- `supervise --once` captures the current tmux output and appends a timestamped progress pointer only when the capture or worker state changed, or when the unchanged heartbeat is due.
- `supervise` without `--once` refuses to run in the coordinator foreground; this prevents unbounded unified exec sessions.
- `start-supervisor` runs the supervisor loop in a dedicated `cw-supervisor:supervisor` tmux session and is the only normal way to start persistent monitoring.
- The supervisor writes `.codex/tmux-workers/status/supervisor.json` with its PID, cycle, interval, and last loop timestamp.
- `--dashboard` refreshes one concise tmux screen with currently present worker windows, current progress summaries, adaptive cadence, and next-check time. It is display-only and is not appended to supervisor logs or management documents.
- The loop automatically widens its capture interval after two consecutive unchanged cycles, up to the heavy-refresh interval or two hours, and resets to the base interval after a capture change, worker-state change, or supervisor query.
- The loop throttles expensive refreshes: schedule/context refresh defaults to every 3600 seconds, and unchanged progress-file appends default to every 7200 seconds.
- Each loop also bounds only state-directory TUI transcripts: closed terminal/orphan logs above about 5 MB are tail-compacted, while active registered logs above about 50 MB are rotated by replacing their `pipe-pane` binding. Open orphan files and registered non-terminal missing-target logs fail closed.
- `supervise --once` does not append a progress entry when the capture and worker state are unchanged.
- `--query-interactive` sends a short progress question only to interactive workers marked `stalled` by default. Use `--query-any-running` only when interrupting active workers is acceptable.
- Use `--query-escape-first` only when you intentionally want the supervisor to send Escape before a query.
- Do not query `codex exec` workers as if they were interactive. For `exec` workers, use capture/log review.
- Use `--query-interval` to avoid interrupting interactive workers too frequently; the default is 3600 seconds.

The default query/continue pair is intentionally short:

```text
Please briefly report current progress in 1-2 sentences. Do not start new work.
Thanks. Please continue the previous task.
```

If the worker is running a foreground command and needs a new instruction, use `interrupt-send` rather than `send`. The ordering matters for Codex TUI: the manager pastes/submits the new message first, then sends `Escape` to switch away from the current run and into the queued instruction.

## Health Supervisor Pattern

Use `start-health-supervisor` in addition to `start-supervisor` for long-lived autonomous sessions. They are complementary:

- `start-supervisor` captures state, writes status/capture artifacts, refreshes schedule/consult context, and can optionally query stalled interactive workers.
- `start-health-supervisor` watches the last pane lines for recoverable Codex errors such as `stream disconnected before completion` and `timeout waiting for child process to exit`, waits for a short stability window, respects a recovery cooldown, then pastes a continuation prompt.

Default health behavior:

- monitors registered workers from `workers.json`
- resolves exact `SESSION:WINDOW.PANE` targets with a pane-safe PID check
- auto-recovers only `mode=interactive` workers
- skips workers with `stopped_at`, while keeping unexpected missing active workers in monitoring
- records state in `.codex/tmux-workers/status/health_supervisor.json`
- records per-target loop memory in `.codex/tmux-workers/status/health_supervisor_state.json`
- logs to `.codex/tmux-workers/logs/health-supervisor.log`
- appends `health-recovery` events to `schedule_events.jsonl`
- with `--dashboard`, displays only currently active health targets and their latest state/action in the tmux window; stopped workers remain excluded

Use `--watch-target main=<SESSION:WINDOW.PANE>` to include the main coordinator pane if the main Codex is itself inside tmux. Use `--observe-target name=<SESSION:WINDOW.PANE>` for panes that should be monitored but never auto-recovered.

Do not use health recovery for quota/auth failures, repeated deterministic crashes, failed tests, bad metrics, merge conflicts, or other semantic blockers. Those require coordinator diagnosis.

## Git Worktree Pattern

Use `--git-worktree` when a worker may edit source code independently. The manager creates a branch and worktree under `.codex/tmux-workers/git-worktrees/<worker>/`, then launches Codex there. The coordinator should later use `collect` and normal git commands to inspect and merge changes.

Do not treat worktree isolation as automatic merge safety. It prevents accidental shared-file overwrites during execution; it does not remove the need for final review.

## Job Registry Pattern

If a worker starts a background process, it must register it with `job-add`. The coordinator can then run `jobs` to see alive/dead PIDs and `job-stop` to send a signal.

Workers should record long-running training/evaluation commands, PIDs, logs, and resources in the job registry before continuing.

## Resume And Collect

Use `resume <worker>` when a tmux target disappears or a worker needs to continue from durable artifacts. The resume prompt points the worker to its workplan, progress, report, inbox, and job registry.

Use `collect` before final decisions. It writes a coordinator summary containing worker states, jobs, progress tails, report tails, and git diff stats.

## Current Limits

This manager coordinates tmux sessions/targets, local files, optional git worktrees, and registered background PIDs. It does not automatically merge worker branches or decide whether metrics pass a project gate. The coordinator must review diffs, logs, tests, and reports before accepting results.

## Autonomous Parallel Mode

When combined with `long-running-autonomous-project-management`, use tmux workers as the outer parallel execution layer:

1. The coordinator reads live project state and writes the active mission.
2. The coordinator launches tmux Codex workers for non-blocking branches:
   - result/log audit
   - report drafting
   - experiment health checks
   - isolated script fixes
   - independent ablations or evaluations with explicit resource assignment
3. The coordinator continues the critical path locally.
4. At each monitoring checkpoint, the coordinator runs `compact-memory --print --context-pack`, `list`, `jobs`, and `progress --lines 20` first. Use `schedule`, `collect --lines 20/30`, and short `capture --lines 80/120` only when the compact summaries are not enough.
5. The coordinator stops stale workers and avoids duplicate jobs.

Do not use tmux workers as a substitute for final review. The coordinator must inspect diffs, logs, metrics, and artifacts before presenting results to the user.
