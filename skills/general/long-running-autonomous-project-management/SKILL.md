---
name: long-running-autonomous-project-management
description: 'Use when a project needs long-running autonomous follow-up: keep the current session alive, launch and monitor experiments or jobs over time, balance GPU/CPU resources, update project docs continuously, default to tmux-launched Codex worker parallelism for non-blocking branch tasks unless disabled or unsuitable, and derive project-specific skills from this template when a specialized workflow is needed.'
---

# Long-Running Autonomous Project Management

## Overview

Use this skill for long-horizon projects that cannot be finished in one short interaction. The job is to keep the project moving: plan, launch, monitor, diagnose, document, and decide the next experiment without losing continuity.

This skill is intentionally generic. It should be specialized only when the project has stable domain rules that deserve a project-specific skill.

## Core Mission

After a target is defined:

1. Keep following the target until the objective is complete or the user explicitly says to exit autonomous follow-up mode.
2. Continue exploring new frames, alternatives, or hypotheses while existing runs are in progress.
3. Never leave the work in an ambiguous half-state when a session or runtime interruption happens.
4. Do not voluntarily exit the current session while still operating under this skill's long-running autonomous follow-up mode.
5. Treat user interruptions, questions, corrections, and tool-wait aborts as interaction inside the same autonomous follow-up mode, not as a stop signal. Handle the new request, then resume monitoring and advancing the original target unless the user explicitly says to stop, pause, or exit autonomous follow-up mode.

## Core Workflow

1. Read the live project state first.
   - Start from current docs, logs, manifests, launchers, checkpoints, and process state.
   - Do not rebuild assumptions from memory alone.
2. Make the active mission explicit.
   - If there is no durable status note, write one before launching new work.
3. Plan resources before launching.
   - Use available GPU aggressively when the task is training or evaluation heavy.
   - Keep enough CPU and IO headroom for dataloading, analysis, logging, and follow-up work.
4. Prefer parallelism when it is useful.
   - Run disjoint experiments in parallel when they answer different questions.
   - Use spare GPU or CPU capacity for monitoring, evaluation, data checks, plotting, or documentation.
   - In autonomous follow-up mode, use `tmux-codex-parallel-workers` by default for independent non-blocking branches while the main session remains coordinator.
   - For actual experiment branches, prefer visible `--worker-kind autonomous-experiment` workers by default. The coordinator should assign resources and scope, then monitor and integrate, rather than duplicating the worker's command-by-command execution.
   - Do not launch tmux workers when the user explicitly disables worker parallelism, when `tmux` or `codex` is unavailable, when quota/cost constraints make it inappropriate, or when the next work is tightly coupled and better handled by the coordinator.
5. Keep a monitoring cadence that matches runtime.
   - Check early and frequently right after launch.
   - Slow down once a run is stable.
   - Keep coordinator-side checks short and bounded. Do not run bare `sleep`, `tail -f`, `watch`, foreground training, or unbounded monitor loops in the main Codex process.
   - Put persistent monitoring into tmux with `start-supervisor`; use `supervise --once` for coordinator-side spot checks.
   - For long-lived Codex tmux runs, also start `start-health-supervisor` so recoverable network/subprocess stalls in interactive Codex panes are resumed without blocking the coordinator.
6. Update documents continuously.
   - Maintain a status doc, an experiment log, and an ideas or exploration doc when the project benefits from persistent memory.
   - For long-running tasks, maintain both a Chinese follow-up file and a Chinese key phase summary file. The follow-up file keeps the chronological operational trail; the key phase summary file captures milestone conclusions, protocols, artifacts, and next-stage decisions.
   - Record the current state, the reason for each launch, the key metric, and the next decision.
   - When recording metrics, always name the model or architecture, data scenario, split or sample scope, input protocol, checkpoint or epoch, and metric meaning. Do not write bare scores without saying what produced them and where they were measured.
   - Every status or log entry must include a concrete timestamp with timezone, such as `2026-05-03 21:40:00 CST`; do not rely on relative wording like "today", "now", or "latest".
7. Decide promotion by gate, not by vibe.
   - Promote only when the candidate clears the project's required hard gate.
   - Do not claim success from a quick subset if the final comparison requires a fuller protocol.

## Required Modules

### 1. State Tracking

Keep a durable, human-readable record of:

- current objective
- active runs
- latest metrics
- hardware usage
- failure state
- next checkpoint

### 2. Resource Planning

Before launching new work, decide:

- which jobs are blocking and which can run in parallel
- whether GPU or CPU is the bottleneck
- whether the next step is training, evaluation, debugging, or documentation
- whether a single session should supervise multiple experiments or only one
- which tmux-launched Codex workers should handle side branches by default, and which write scopes/resource ownership each worker receives before launch

### 3. Monitoring Loop

Use an adaptive loop:

- short interval after launch
- wider interval for stable training
- tighter interval near promotion, regression, or early stopping points

During each checkpoint:

- inspect process health
- inspect log growth
- inspect key metrics
- decide whether to keep waiting, restart, or pivot

### 4. Exploration Loop

While waiting:

- think through alternative frameworks
- record viable ideas in the project's exploration doc
- identify the next experiment that is worth running
- prefer changes that isolate one major factor at a time

### 4b. Default Tmux Codex Worker Layer

Use this layer by default in autonomous follow-up mode. The coordinator should normally activate `tmux-codex-parallel-workers` after reading live project state, then keep the current session as the planner, reviewer, integrator, and user-facing decision maker.

Skip or postpone this layer only when the user explicitly opts out, the environment lacks `tmux` or `codex`, resource/cost limits make extra Codex processes inappropriate, or the task has no independent side branch worth parallelizing.

1. Load `tmux-codex-parallel-workers`.
2. Initialize a project-local worker state directory, normally `.codex/tmux-workers/`.
3. Start the dedicated read-only user consultation worker with `start-consult` when the run is expected to be long-lived or user auditability matters.
4. Launch workers for independent branches that do not block the coordinator's next action. For real experiment branches, prefer `tmux-codex-parallel-workers launch --worker-kind autonomous-experiment` so the worker's Codex operation process remains visible in tmux.
   - In this environment, launch/resume project-local experiment workers with YOLO-level permissions (`sandbox=danger-full-access`, `approval=never`) unless the user explicitly asks for a restricted worker. This avoids the read-only `.codex` mount and blocked SSH failure mode seen under `workspace-write`.
   - Even under YOLO permissions, worker prompts must avoid destructive filesystem operations such as `rm` unless the coordinator explicitly authorizes a narrowly scoped cleanup.
   - Prefer keeping visible worker panes on the local/nature host. Workers should operate other machines through SSH from the local pane, so the user can monitor, interrupt, and clean all worker windows in one local tmux session.
5. Assign every worker:
   - objective
   - working directory
   - read/write scope
   - GPU/CPU/port/output ownership when relevant
   - expected completion report
6. Maintain `.codex/tmux-workers/COORDINATOR_SCHEDULE.md` as the user-auditable control document for starts, stops, task assignment, scheduling decisions, and results.
7. Keep `.codex/tmux-workers/consult/CONSULT_CONTEXT.md` fresh so the consultation worker can answer user questions without interrupting the coordinator.
8. Keep the coordinator on the critical path while workers run.
9. At each monitoring checkpoint, list and capture worker outputs, inspect any changed files, integrate safe results, refresh the consultation context, and record the decision in the follow-up file.
10. Stop stale, duplicate, failed, or superseded workers instead of letting old tmux windows accumulate.
11. Never run the supervisor infinite loop directly in the coordinator; only `start-supervisor` may run the long-lived loop, and it must do so inside tmux.
12. When a busy interactive worker must be redirected immediately, use `tmux-codex-parallel-workers interrupt-send`; it submits the new message first, then sends `Escape` so Codex switches to the queued instruction.
13. For long-lived autonomous operation, start `tmux-codex-parallel-workers start-health-supervisor` after the worker layer is initialized. Add the main coordinator pane with `--watch-target main=<SESSION:WINDOW.PANE>` only when the main Codex itself is running inside tmux and should be auto-recovered.
14. Use the coordinator as the control plane:
   - decide which branch is worth running;
   - cap GPU/CPU/IO usage;
   - issue timely `send` / `interrupt-send` instructions when a worker needs a protocol correction or a new checkpoint;
   - collect reports and reconcile results;
   - update durable docs and promotion decisions.
15. Use workers as execution planes:
   - launch/monitor assigned experiments;
   - run bounded audits or sweeps inside their write scope;
   - keep progress/report files current;
   - register background jobs when supported.
16. Current job registration caveat: `job-add` tracks local PIDs directly. For remote tmux jobs on another host, workers must additionally record the host, tmux session, GPU, command, log path, result/checkpoint roots, and liveness/polling command in progress/report/schedule. Treat `pid=0` job entries only as remote markers unless the project-specific manager has first-class remote liveness checks.

### 5. Failure Handling

If a run fails:

- inspect the failure logs before restarting
- distinguish launch failure from training regression
- record the cause and the cleanup performed
- avoid duplicate or zombie jobs

### 6. Documentation Discipline

Every meaningful update should include:

- concrete timestamp with timezone
- model or architecture name
- data scope
- split, sample cap, checkpoint or epoch for reported metrics
- framework or architecture
- why the run exists
- the important metric or gate
- the next action

For sustained autonomous follow-up, documentation must include two separate Chinese artifacts:

- A follow-up file that is updated continuously with timestamped launches, monitoring checkpoints, failures, decisions, and next actions.
- A key phase summary file that is updated at each milestone or phase boundary as a complete summary document. It must explain what the task is, what data is used, what model framework is used, what training framework or protocol is used, what the stage results show, what artifact paths matter, what risks remain, and what the next plan is.

Do not let one document replace the other. The follow-up file is the operational timeline; the key phase summary file is the compact, restartable conclusion record.

### 7. Follow-up Report Format

When the project needs a dedicated follow-up report document, structure it like a durable progress report, following the pattern of `docs/<project-followup-report>.md`:

- Write the document in Chinese by default, except for code names, model names, metric names, and necessary technical terms.
- Use the document title and initial sections for the stable target, motivation, framework, data scope, gates, and risks.
- Append one subsection per key progress event, not one unstructured running paragraph.
- Each progress subsection title must include a concrete timestamp with timezone, for example `### 2026-05-03 21:40:00 CST Update: medium-run validation completed`.
- Each progress subsection should record the evidence inspected, decision made, metric or failure signal, and next checkpoint.
- For deep learning or other experiment-heavy work, a key progress subsection must not report metrics alone. It must also name the model architecture, code path, data scenario, split/sample scope, checkpoint or epoch, input protocol, training and evaluation protocol, and improvement motivation so an external reader can reproduce and understand the change.
- Keep later updates append-only unless correcting a factual error; preserve the chronological trail.

### 8. Key Phase Summary Format

When a task reaches a meaningful phase boundary, update a separate key phase summary document in Chinese. This file is not a brief note; it must be a complete, restartable summary document that lets a later session or external reader understand the stage without reconstructing context from logs.

- State the task definition: the target, motivation, current phase boundary, and conclusion.
- Explain the data: source, generation or collection process, preprocessing, train/validation/test split, sample scale, scenario coverage, and important paths.
- Explain the model framework: architecture, key modules, inputs and outputs, major parameters, and why this framework was chosen or changed.
- Explain the training framework or protocol: scripts, launch commands or entry points, loss functions, optimizer/scheduler if relevant, epochs or stopping criteria, evaluation protocol, checkpoints, and runtime environment.
- Summarize the stage results: metrics, comparisons, qualitative findings, failures, regression signals, and whether the result passes the current gate.
- Record artifact paths: datasets, scripts, configs, checkpoints, logs, TensorBoard runs, result directories, figures, and reports.
- Summarize remaining risks, open questions, and known limitations.
- End with the next phase plan: recommended action, priority, required resources, and the concrete next checkpoint.
- Prefer updating this file at milestones instead of copying every monitoring checkpoint from the follow-up file.

## Session Policy

- Keep the current work session alive for the whole long-running task.
- Treat autonomous follow-up mode as persistent. Do not end the current session by yourself while this mode is active, even after finishing a checkpoint, run launch, evaluation, documentation update, or intermediate user instruction.
- If the user gives an intermediate instruction, asks a side question, corrects a detail, or intentionally interrupts a waiting/monitoring command, handle it, then resume continuous follow-up toward the overall target.
- An interruption is not an instruction to stop. Only explicit wording such as "停止自主跟进", "暂停当前任务", "退出自主跟进模式", "stop following", or "exit autonomous follow-up mode" ends or pauses the mode.
- Stop completely only when the user explicitly says to exit autonomous follow-up mode, such as "退出自主跟进模式" or "exit autonomous follow-up mode".
- Use bounded spot checks and tmux-managed background monitors instead of foreground waits in the coordinator process.

## When To Specialize

Use this generic skill as-is for broad project management and follow-up.

Create a specialized skill when the project has stable recurring rules, named artifacts, or domain-specific gates. When you derive a specialized skill:

1. Define the final target explicitly before writing the derived skill. State what outcome the autonomous follow-up mode is trying to reach, not only which project or artifacts it will monitor.
2. Choose a concrete derived skill name from the final target if the user did not provide one. The user does not need to specify the name.
3. Say what it specializes from this template.
4. State the generated or user-provided derived skill name explicitly in the derived skill.
5. Put the final target in the derived skill's mission or operating rules so future sessions know what "keep going" means.
6. Keep this general workflow as the base, then add only the project-specific rules.
7. Include the project-specific Chinese follow-up file path and Chinese key phase summary file path in the derived skill's documentation rules.
8. Preserve the autonomous-interruption rule as a hard requirement: user questions, corrections, or aborted waits are intermediate interactions; after handling them, the derived skill must resume the original follow-up unless the user explicitly stops, pauses, or exits autonomous follow-up mode.
9. Preserve the default tmux Codex worker layer unless the specialized domain has a concrete reason to disable worker parallelism.

Example derivation:

- Derived skill name: `diffusion-searcher-autonomous-adaptive-noise`
- Specialization: `diffusion-searcher` adaptive-noise experiments, project paths, launchers, TensorBoard hygiene, and OOD gates

## Reference

See [references/workflow.md](references/workflow.md) for the reusable operating procedure, monitoring cadence, documentation pattern, and derivation checklist.
