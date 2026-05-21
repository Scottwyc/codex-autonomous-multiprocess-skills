# Tmux Codex Worker Scripts

These scripts are the execution layer for the `tmux-codex-parallel-workers` skill. They are required for the skill to launch, supervise, recover, and coordinate tmux-hosted Codex worker processes.

## `codex_tmux_manager.py`

Primary orchestration command.

Responsibilities:

- initialize a project-local worker state directory
- launch `codex exec` or interactive Codex workers in tmux windows
- support visible `autonomous-experiment` workers
- write worker prompts, progress files, reports, status files, logs, inbox messages, and job registries
- maintain `COORDINATOR_SCHEDULE.md`
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
  --session codex-workers \
  init --cwd "$PWD" --mission "Coordinate a long-running autonomous project."

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  launch audit-a \
  --cwd "$PWD" \
  --write-scope "read-only audit and report" \
  --task "Inspect logs and write a concise report."
```

Common commands:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers list
python "$MANAGER" --state-dir .codex/tmux-workers capture audit-a --lines 160
python "$MANAGER" --state-dir .codex/tmux-workers progress audit-a
python "$MANAGER" --state-dir .codex/tmux-workers send audit-a "Continue with the summary only."
python "$MANAGER" --state-dir .codex/tmux-workers interrupt-send audit-a "Stop current expansion and report current state."
python "$MANAGER" --state-dir .codex/tmux-workers collect
```

Long-running monitors:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session codex-workers start-supervisor --interval 300
python "$MANAGER" --state-dir .codex/tmux-workers --session codex-workers start-health-supervisor --interval 30
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

Direct dry-run:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_health_supervisor.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  --once --dry-run
```

Include a main coordinator pane when the coordinator itself runs inside tmux:

```bash
python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  start-health-supervisor \
  --watch-target main=<SESSION:WINDOW.PANE>
```

Use `--observe-target name=<SESSION:WINDOW.PANE>` for panes that should be logged but never receive recovery prompts.

## State Files

Runtime state is written under the selected `--state-dir`, normally:

```text
.codex/tmux-workers/
```

Important files:

- `workers.json`: worker registry
- `COORDINATOR_SCHEDULE.md`: user-auditable coordinator plan
- `schedule_events.jsonl`: coordinator and supervisor events
- `progress/<worker>.md`: worker progress
- `reports/<worker>.md`: worker final or intermediate report
- `inbox/<worker>/`: auditable coordinator messages
- `status/<worker>.json`: worker status
- `status/supervisor.json`: normal supervisor status
- `status/health_supervisor.json`: health supervisor status
- `jobs/<worker>.json`: background job registry
- `captures/<worker>/`: supervisor captures

