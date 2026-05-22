# Tmux Codex Worker Scripts

These scripts are the execution layer for the `tmux-codex-parallel-workers` skill. They are required for the skill to launch, supervise, recover, and coordinate tmux-hosted Codex worker processes.

## `codex_tmux_manager.py`

Primary orchestration command.

Responsibilities:

- initialize a project-local worker state directory
- launch `codex exec` or interactive Codex workers in independent tmux sessions by default
- use the short `cw-` tmux session prefix by default, for example `cw-child-a:codex`
- use normal Codex alternate-screen TUI for interactive workers by default; `--inline-tui` is opt-in for forwarding Codex `--no-alt-screen`
- support visible `autonomous-experiment` workers
- support subordinate `branch-manager` workers for major branches
- write manager-mediated `peer-send` messages between workers
- write worker prompts, progress files, reports, status files, logs, inbox messages, and job registries
- maintain `COORDINATOR_CONSTRAINTS.md` as the unified constraints file all launched Codex processes read first
- maintain `COORDINATOR_SCHEDULE.md`
- maintain `COORDINATOR_CONTEXT_PACK.md` and `COORDINATOR_MEMORY.md` as compact coordinator memory
- maintain `COORDINATOR_RECOVERY.md` and restart a registered main coordinator after context-window exhaustion
- start read-only consultation workers
- start/stop the normal supervisor and health supervisor
- send regular or interrupting coordinator messages
- resume disappeared workers from durable artifacts
- collect worker reports and job state for coordinator review

Basic usage:

```bash
MANAGER="${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py"

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  init --cwd "$PWD" --mission "Coordinate a long-running autonomous project."

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch audit-a \
  --cwd "$PWD" \
  --write-scope "read-only audit and report" \
  --task "Inspect logs and write a concise report."
```

Branch manager and peer communication:

```bash
python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch branch-mgr \
  --cwd "$PWD" \
  --worker-kind branch-manager \
  --manager-scope "Coordinate one major branch and summarize child worker results." \
  --task "Plan child workers, launch them with --parent-worker branch-mgr, and maintain a branch-level report."

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch child-a \
  --cwd "$PWD" \
  --parent-worker branch-mgr \
  --worker-kind autonomous-experiment \
  --task "Run one bounded child experiment and report evidence paths."

tmux ls | rg '^cw(-|:)'
tmux attach -t cw-child-a
# From inside tmux:
tmux switch-client -t cw-child-a:codex

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  peer-send child-a child-b \
  --message "Child A produced artifact path results/child-a/metrics.json for Child B to inspect." \
  --notify
```

If an older interactive worker was launched with inline TUI and the bottom Codex prompt/status line is missing, restart that worker from durable state:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session cw stop child-a
python "$MANAGER" --state-dir .codex/tmux-workers --session cw resume child-a --mode interactive
```

Common commands:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers list
python "$MANAGER" --state-dir .codex/tmux-workers constraints --print
python "$MANAGER" --state-dir .codex/tmux-workers constraints --tensorboard-port-range 16006-16099
python "$MANAGER" --state-dir .codex/tmux-workers compact-memory --print --context-pack
python "$MANAGER" --state-dir .codex/tmux-workers compact-memory --note "Audit branch is waiting on metrics." --decision "Do not launch duplicate audit workers." --next-action "Check jobs and compact memory at next checkpoint."
python "$MANAGER" --state-dir .codex/tmux-workers progress audit-a --lines 20
python "$MANAGER" --state-dir .codex/tmux-workers capture audit-a --lines 80
python "$MANAGER" --state-dir .codex/tmux-workers send audit-a "Continue with the summary only."
python "$MANAGER" --state-dir .codex/tmux-workers interrupt-send audit-a "Stop current expansion and report current state."
python "$MANAGER" --state-dir .codex/tmux-workers peer-send audit-a audit-b --message "Use artifact path results/audit-a/summary.md."
python "$MANAGER" --state-dir .codex/tmux-workers collect --lines 30
```

Main coordinator restart handoff:

```bash
tmux display-message -p '#S:#W.#{pane_index}'

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  register-coordinator \
  --target <SESSION:WINDOW.PANE> \
  --cwd "$PWD" \
  --mission "Coordinate a long-running autonomous project."

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor \
  --restart-main-on-context-full \
  --restart-main-when-missing
```

If the registered coordinator pane shows `Codex ran out of room in the model's context window`, or the registered coordinator target disappears while `--restart-main-when-missing` is enabled, the health supervisor calls `recover-coordinator`, refreshes `COORDINATOR_RECOVERY.md`, closes the old pane by default when present, and launches a new coordinator window. The recovered coordinator starts from `COORDINATOR_CONTEXT_PACK.md`, `COORDINATOR_MEMORY.md`, the schedule, worker registry, progress/report files, jobs, peer messages, branch-manager summaries, and consultation context.

Manual recovery:

```bash
python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  recover-coordinator --reason manual-restart --kill-old
```

Context budget defaults:

- `COORDINATOR_CONSTRAINTS.md` is the project-wide operating contract. Workers, branch managers, resumed workers, consultation workers, and recovered coordinators are prompted to read it before their task-specific instructions.
- `progress` defaults to concise tails; use larger `--lines` only for diagnosis.
- `COORDINATOR_CONTEXT_PACK.md` is the shortest reload packet; `COORDINATOR_MEMORY.md` is the coordinator's compact working memory.
- `COORDINATOR_SCHEDULE.md` and `CONSULT_CONTEXT.md` contain summaries and evidence paths, not full worker transcripts.
- Run `compact-memory --note ... --decision ... --next-action ...` after meaningful coordinator decisions so later checkpoints do not rely on chat history.
- Noisy outputs should be written to logs/artifacts; workers should cite paths and summarize key evidence.

Long-running monitors:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session cw start-supervisor --interval 300
python "$MANAGER" --state-dir .codex/tmux-workers --session cw start-health-supervisor --interval 30 --restart-main-on-context-full --restart-main-when-missing
```

## `codex_tmux_health_supervisor.py`

Health recovery loop for tmux-hosted Codex panes.

It watches the last lines of registered worker panes and optional extra panes for recoverable Codex errors such as:

- `stream disconnected before completion`
- `timeout waiting for child process to exit`
- `connection closed before message completed`
- `error sending request`
- `network error`
- `ECONNRESET`
- `ETIMEDOUT`
- `502 Bad Gateway`
- `503 Service Unavailable`

It waits for a stability window and respects a cooldown before sending a recovery prompt. It auto-recovers only interactive Codex panes by default.

It also monitors the registered main coordinator target unless `--no-coordinator` is passed. Context-window exhaustion is treated as fatal to the old coordinator thread, so auto-recovery requires `--restart-main-on-context-full` and launches a new coordinator via the manager's durable recovery prompt instead of pasting a continuation prompt into the exhausted pane. If the registered coordinator pane disappears outright, `--restart-main-when-missing` enables the same recovery path.

Direct dry-run:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_health_supervisor.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  --once --dry-run
```

Include a main coordinator pane when the coordinator itself runs inside tmux:

```bash
tmux display-message -p '#S:#W.#{pane_index}'

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  register-coordinator --target <SESSION:WINDOW.PANE> --cwd "$PWD"

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor \
  --restart-main-on-context-full \
  --restart-main-when-missing
```

Use `--observe-target name=<SESSION:WINDOW.PANE>` for panes that should be logged but never receive recovery prompts.

## State Files

Runtime state is written under the selected `--state-dir`, normally:

```text
.codex/tmux-workers/
```

Important files:

- `workers.json`: worker registry
- `COORDINATOR_CONSTRAINTS.md`: unified constraints loaded by all launched Codex processes
- `coordinator_constraints_events.jsonl`: append-only constraints changes
- `COORDINATOR_CONTEXT_PACK.md`: shortest reload packet for the main coordinator
- `COORDINATOR_MEMORY.md`: compact coordinator working memory
- `COORDINATOR_SCHEDULE.md`: user-auditable coordinator plan
- `COORDINATOR_RECOVERY.md`: restart handoff for a recovered main coordinator
- `coordinator_memory_events.jsonl`: append-only compact memory notes and decisions
- `schedule_events.jsonl`: coordinator and supervisor events
- `peer_messages.jsonl`: manager-owned worker-to-worker message log
- `progress/<worker>.md`: worker progress
- `reports/<worker>.md`: worker final or intermediate report
- `inbox/<worker>/`: auditable coordinator messages
- `status/<worker>.json`: worker status
- `status/supervisor.json`: normal supervisor status
- `status/health_supervisor.json`: health supervisor status
- `jobs/<worker>.json`: background job registry
- `captures/<worker>/`: supervisor captures
