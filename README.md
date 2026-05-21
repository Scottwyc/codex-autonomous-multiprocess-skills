# Codex Autonomous Multiprocess Skills

This repository packages two Codex skills for long-running autonomous work with tmux-managed Codex worker processes.

## Included Skills

- `long-running-autonomous-project-management`
  - Generic long-running autonomous project management workflow.
  - Defaults to tmux-launched Codex workers for useful non-blocking branch tasks.
  - Defines coordinator responsibilities, monitoring cadence, documentation discipline, and failure handling.

- `tmux-codex-parallel-workers`
  - Launches, supervises, health-monitors, interrupts, resumes, and stops independent Codex CLI workers in tmux windows.
  - Supports visible `autonomous-experiment` workers, read-only consultation workers, coordinator scheduling docs, worker progress/report files, background job registries, optional git worktrees, and health recovery for transient Codex pane errors.

Together, the two skills form an autonomous multiprocess management framework: a main Codex coordinator keeps final judgment and integration authority, while separate tmux Codex workers execute branch tasks in parallel.

## Repository Layout

```text
.
├── scripts/
│   ├── install.sh
│   └── validate.sh
└── skills/general/
    ├── long-running-autonomous-project-management/
    │   ├── SKILL.md
    │   ├── agents/openai.yaml
    │   └── references/workflow.md
    └── tmux-codex-parallel-workers/
        ├── SKILL.md
        ├── agents/openai.yaml
        ├── references/
        │   ├── codex-tmux-framework.zh.md
        │   └── worker-protocol.md
        └── scripts/
            ├── README.md
            ├── codex_tmux_manager.py
            └── codex_tmux_health_supervisor.py
```

The Python scripts under `tmux-codex-parallel-workers/scripts/` are not optional examples. They are the deterministic orchestration layer used by the skill.

## Core Scripts

### `codex_tmux_manager.py`

Main tmux Codex worker manager. It provides commands to initialize state, launch workers, send or interrupt prompts, start consultation windows, start normal and health supervisors, track background jobs, resume workers, collect reports, and maintain the coordinator schedule document.

Typical command:

```bash
MANAGER="${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py"

python "$MANAGER" --state-dir .codex/tmux-workers --session codex-workers init --cwd "$PWD"
python "$MANAGER" --state-dir .codex/tmux-workers --session codex-workers launch worker-a --cwd "$PWD" --task "Do one bounded branch task and report back."
```

### `codex_tmux_health_supervisor.py`

Low-level health supervisor used by the manager's `start-health-supervisor` command. It monitors tmux Codex panes for recoverable transport/subprocess failures and sends a bounded continuation prompt when a pane is stuck.

Typical direct dry-run:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_health_supervisor.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  --once --dry-run
```

Prefer invoking it through the manager for real long-running use:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session codex-workers start-health-supervisor --interval 30
```

See [the script README](skills/general/tmux-codex-parallel-workers/scripts/README.md) for the command inventory and operational notes.

## Requirements

- Codex CLI available as `codex`
- `tmux`
- Python 3.10+
- A Unix-like shell environment

Optional but useful:

- `git`, for worktree-based worker isolation
- `rg`, for fast local search

## Install

From this repository root:

```bash
./scripts/install.sh
```

The installer copies the two skills into:

```text
${CODEX_HOME:-$HOME/.codex}/skills/general/
```

If an older copy exists, it is moved to a timestamped backup under:

```text
${CODEX_HOME:-$HOME/.codex}/skills/.backup/
```

## Validate

```bash
./scripts/validate.sh
```

This checks basic skill frontmatter and compiles bundled Python scripts.

## Quick Start

After installing, start by asking Codex for long-running autonomous project management or tmux Codex parallel workers. For direct manager usage:

```bash
MANAGER="${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py"

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  init --cwd "$PWD" --mission "Coordinate a long-running autonomous project."

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  launch branch-a \
  --cwd "$PWD" \
  --worker-kind autonomous-experiment \
  --write-scope "Run one bounded experiment branch and report results." \
  --task "Inspect the project, run a small validation experiment, update progress, and write a report."
```

For long-lived sessions, also start the two monitor layers:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session codex-workers start-supervisor --interval 300
python "$MANAGER" --state-dir .codex/tmux-workers --session codex-workers start-health-supervisor --interval 30
```

Attach to the tmux session:

```bash
tmux attach -t codex-workers
```

## Safety Model

- The coordinator owns task decomposition, final review, integration, and user-facing conclusions.
- Workers must receive bounded objectives, write scopes, resource ownership, and expected reports.
- The framework records worker state under `.codex/tmux-workers/` so users can audit launches, inbox messages, progress, reports, captures, jobs, and scheduling decisions.
- The health supervisor only targets transient Codex pane stalls such as network disconnects or child-process timeout errors. It is not a replacement for debugging quota/auth failures, failed tests, merge conflicts, or bad metrics.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
