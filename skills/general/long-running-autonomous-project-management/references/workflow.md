# Workflow

## Purpose

This reference describes a reusable autonomous follow-up loop for long-running projects. It is generic on purpose so it can be specialized for any project that needs sustained progress over hours, days, or weeks.

## Operating Pattern

1. Define the objective.
   - Write down the target outcome and the current constraint.
   - Treat this as the mission until the user explicitly says to exit autonomous follow-up mode.
2. Read the live state.
   - Inspect current docs, logs, processes, checkpoints, and resource usage.
   - Do not rely on stale memory if current state is available locally.
3. Launch the next useful action.
   - Start the next experiment, evaluation, data check, or refactor that advances the objective.
   - By default, organize and schedule non-blocking branch workers through `tmux-codex-parallel-workers` and keep the current session as coordinator.
   - Skip tmux workers only when the user disables worker parallelism, the environment lacks `tmux` or `codex`, cost/quota constraints make extra Codex processes inappropriate, or there is no independent side branch worth parallelizing.
   - Before launching workers, refresh `COORDINATOR_CONSTRAINTS.md` with coordinator-wide rules that every child process must load first, including resource ownership, TensorBoard/dashboard safe ports, bind hosts, output roots, cleanup limits, and remote-job conventions.
   - If the main coordinator itself is running inside tmux, register its exact `SESSION:WINDOW.PANE` target with `register-coordinator` so an in-place recovered coordinator can read `COORDINATOR_RECOVERY.md` and take over existing workers instead of rediscovering everything from scratch.
   - For long-lived work, start the read-only consultation worker so the user can ask status and evidence questions in its dedicated tmux session without interrupting the coordinator.
   - For experiment branches, prefer visible `--worker-kind autonomous-experiment` workers so the user can attach to tmux and watch the Codex worker's actual planning, checks, commands, diagnostics, and handoff.
   - Keep visible interactive workers on the manager default normal Codex TUI so the bottom prompt/status line stays visible; use `--inline-tui` only when inline scrollback matters more than TUI stability.
   - For major branches, prefer `--worker-kind branch-manager` first. The branch manager receives the branch target and resource envelope, then launches child `autonomous-experiment` workers with `--parent-worker` and coordinates their peer messages and branch report.
   - Treat coordinator context as scarce: worker updates should be summaries with evidence paths; raw logs, full diffs, long tables, and complete transcripts should remain in files unless explicitly needed.
   - Keep `COORDINATOR_CONTEXT_PACK.md` and `COORDINATOR_MEMORY.md` refreshed with `compact-memory`; use them as the coordinator's short working memory before loading larger artifacts.
   - When a busy interactive worker needs an immediate redirect, use `interrupt-send`: the manager submits the new message first, then sends `Escape` so Codex switches to the queued instruction.
   - For long-lived tmux Codex operation, start `start-health-supervisor` to recover interactive panes stuck on known transient Codex network/subprocess errors. When the main Codex is registered inside tmux, use `--restart-main-on-context-full`; this respawns the exact registered pane in place. Add `--restart-main-when-missing` only when coordinator-wide constraints explicitly authorize a new target after the registered target is confirmed absent.
4. Monitor on a cadence.
   - Early launch or active failure diagnosis: check frequently.
   - Stable run or known future gate: widen the interval and align the next check with the gate.
   - After two unchanged checks, widen again. Near completion, regression, failure, or a decision point, tighten the cadence.
   - Do not write a checkpoint when state, evidence, decision, resource ownership, failure risk, and next action are unchanged.
   - Main-coordinator checks must be short and bounded. Use `compact-memory --print --context-pack`, `supervise --once`, `jobs`, `progress --lines 20`, and short log tails. Use `collect --lines 20/30` or larger schedule/capture only when compact memory is insufficient.
   - Persistent monitor loops must run through `start-supervisor` or `start-health-supervisor` in tmux, not as foreground commands in the coordinator process.
5. Fill idle time with exploration.
   - Draft next ideas.
   - Reconcile open questions.
   - Prepare the next candidate.
6. Update docs continuously.
   - Status doc: current state and next checkpoint
   - Log doc: launches, failures, restarts, promotions
   - Ideas doc: hypotheses and new directions
   - Chinese follow-up file: chronological operational trail for launches, monitoring, failures, decisions, and next actions
   - Chinese key phase summary file: a complete summary document covering the task definition, data, model framework, training framework or protocol, stage results, artifact paths, risks, and next-stage plan
   - User-facing key-results dashboard: concise current-state target index with clickable result documents, result directories, active progress, and next gates
   - Use a concrete timestamp with timezone on every status or log entry, for example `2026-05-03 21:40:00 CST`.
   - Update operational docs only at meaningful events. Repeated unchanged polls must not create follow-up, schedule, consultation, or compact-memory entries.
   - Keep schedule, context pack, compact memory, consultation context, and recovery handoff as bounded current-state views. Put full history in event logs, worker reports, or timestamped archives.
   - For a dedicated follow-up report, use append-only progress subsections. Each subsection should be one key progress event, and its heading must include a concrete timestamp with timezone, for example `### 2026-05-03 21:40:00 CST Update: validation finished`.
   - Do not let the follow-up file, key phase summary, and key-results dashboard collapse into one artifact. The dashboard is navigation and status, not an operational timeline.
7. Repeat while autonomous follow-up mode is active.
   - Do not voluntarily exit the current session.
   - If the user gives an intermediate instruction, complete it, then resume the overall objective.
   - Stop completely only when the user explicitly says to exit autonomous follow-up mode, such as "退出自主跟进模式" or "exit autonomous follow-up mode".

## Coordinator Context Construction

Build coordinator context from the smallest durable view that can answer the current question:

1. Read `COORDINATOR_CONTEXT_PACK.md`.
2. Escalate to compact memory, jobs, and short worker progress/report summaries only when needed.
3. Load schedule, captures, raw logs, full diffs, or large artifacts only for a concrete diagnosis, integration decision, or user audit.
4. After a meaningful decision, rewrite bounded current-state views and point to evidence paths. Do not copy full history into them.

Use these retention defaults:

- Keep every non-terminal worker plus only a small recent terminal tail in `workers.json`; archive a complete snapshot before pruning.
- Compact the live registry around 1 MB or 128 records. Investigate and compact current-state Markdown around 1 MB or 5,000 lines.
- Rotate event and supervisor logs around 1 MB.
- Bound `.codex/tmux-workers/logs/*.log` TUI transcripts separately from experiment/job logs: compact closed terminal/orphan transcripts, safely rotate active registered transcripts, and fail closed on open unregistered files.
- Keep one watcher per current decision gate and one supervisor per state directory. Stop superseded temporary windows.

## User-Facing Key Results Dashboard

Keep one stable project-owned dashboard, normally `docs/<project>-key-results-dashboard.md`. It is separate from `.codex/tmux-workers/COORDINATOR_SCHEDULE.md`: the schedule explains control-plane activity, while the key-results dashboard explains what the project has achieved and where the evidence lives.

Required current-state views:

- target overview: status, accepted conclusion, key result docs, result directory, active progress, next gate
- active work: owner/branch, current milestone, expected output, next meaningful check
- completed result index: direct links to external-reader reports, phase summaries, figures, and canonical result roots
- blockers and claim boundaries: why an item is not complete and what evidence would change that

Use relative Markdown links whenever possible. Prefer readable reports before machine result directories. Write `待生成`, `无`, or `未授权` instead of creating a broken link. Refresh only on material changes and validate local links with `scripts/validate_user_results_dashboard.py`.

## Resource Planning Heuristics

- If GPU is free, prefer launching meaningful work rather than leaving the system idle.
- If GPU is full but CPU is spare, use CPU for evaluation, analysis, cleanup, plotting, or documentation.
- If CPU becomes the bottleneck, avoid launching more dataloader-heavy jobs.
- Prefer a few disjoint experiments over many nearly identical ones.
- In autonomous follow-up mode, assume tmux Codex workers are the default route for useful side branches. Give each worker a disjoint write scope and explicit resource ownership before launch.
- Use branch-manager workers when a branch would otherwise require the main coordinator to track many child runs directly. Give the branch manager explicit `--manager-scope`, owned output roots, and resource limits.
- Let front-line workers communicate through `peer-send` for short evidence paths, blockers, and dependency notices. Scope/resource changes still require a branch-manager or main-coordinator scheduling decision.
- Keep `.codex/tmux-workers/COORDINATOR_SCHEDULE.md` and `.codex/tmux-workers/consult/CONSULT_CONTEXT.md` current so worker state, scheduling decisions, and user-consultation answers remain auditable.
- Keep `.codex/tmux-workers/COORDINATOR_CONTEXT_PACK.md` and `.codex/tmux-workers/COORDINATOR_MEMORY.md` current so the main coordinator can compress working memory and avoid relying on long chat history.
- Keep `.codex/tmux-workers/COORDINATOR_CONSTRAINTS.md` current so all launched/resumed child processes inherit the same resource, safety, TensorBoard, output, remote-job, and cleanup constraints before task-specific prompts.
- Keep `.codex/tmux-workers/COORDINATOR_RECOVERY.md` current so a recovered main coordinator can recover the mission, worker graph, resources, jobs, branch summaries, peer messages, and next checkpoints after the old thread exhausts context. Preserve the exact registered tmux target whenever it is still present.
- Keep compact memory, schedule, and consultation context compact. They should contain worker status, concise report/progress excerpts, decisions, next checkpoints, and paths to evidence, not full tmux scrollback or raw experiment logs.
- After every meaningful decision or phase checkpoint, run `compact-memory --note ... --decision ... --next-action ...`; this preserves the decision outside the model context window.
- Do not call `compact-memory`, `schedule-note`, `consult-sync`, or schedule refresh merely to record that nothing changed.
- Treat `workers.json` as current state, not the complete historical ledger. Archive before compaction and use `compact-registry` to repair older state directories.
- When global operating rules change, run `constraints --append ...` or a targeted helper such as `constraints --tensorboard-port-range 16006-16099`, then record the reason with `schedule-note`.
- Do not keep the coordinator alive with bare `sleep`, `tail -f`, `watch`, foreground training, or unbounded Python loops. Put those jobs in tmux or background processes with registered PID/log/resource ownership.

## Monitoring Cadence

Use progress and the next decision gate to choose the interval.

- Very fresh launch or active failure diagnosis: about 5-10 minutes
- Stable progress: about 15-30 minutes
- Long stable job or known future gate: about 30-120 minutes, aligned with the gate
- Two unchanged checks: widen the next interval
- Material change or failure: reset to a shorter interval until stable

During each check, verify:

- process exists
- logs are updating
- metrics are sensible
- no duplicate or zombie jobs have appeared
- tmux Codex workers and branch-manager hierarchies are still relevant, and their captured output has been reviewed before any integration decision
- branch managers have summarized child results clearly enough that the main coordinator does not need to load every child transcript
- peer messages in `peer_messages.jsonl` do not imply unrecorded scope/resource changes
- the dedicated consultation worker has refreshed context after major scheduling decisions, if it is running
- the coordinator has loaded only the smallest necessary evidence slice; escalate from summary to short tail to full artifact only when needed

## Decision Gates

Use the project's own gates, but keep this order:

1. Health gate
   - Did the run start correctly and keep running?
2. Local improvement gate
   - Does the candidate beat the previous version on the relevant local metric?
3. Hard-case gate
   - Does it survive the difficult or out-of-distribution case that matters to the project?
4. Full comparison gate
   - Is it still better when compared under the final protocol?

Do not promote based on a weaker gate if the final protocol is stricter.

## Failure Handling

If something goes wrong:

- read the failure evidence first
- decide whether to retry, reconfigure, or abandon
- record the timestamped reason in the log
- keep the next session restartable

## Follow-up Report Pattern

Use this pattern for dedicated long-running report documents:

1. Write in Chinese by default, except for code names, model names, metric names, and necessary technical terms.
2. Start with stable context sections: target, motivation, framework, data, gates, and risks.
3. Append every important later event as its own subsection.
4. Put the exact timestamp and timezone in the subsection heading, not only in the body.
5. Inside each subsection, include evidence, metrics, decision, and next checkpoint.
6. For deep learning or other experiment-heavy work, do not write metric-only updates. Key subsections must include the new model architecture or code path, data scenario, training and evaluation protocol, and improvement motivation so an external reader can reproduce and understand the change.

Example heading:

`### 2026-05-03 21:40:00 CST Update: Stage B evaluation completed`

## Key Phase Summary Pattern

Maintain a separate Chinese key phase summary document for each long-running task or task line. Update it at meaningful milestones or phase boundaries, not for every routine monitoring check. This is not a brief note; it must be a complete, restartable summary document that lets a later session or external reader understand the stage without reconstructing context from logs.

Each update should include:

- task definition: target, motivation, phase boundary, and conclusion
- data: source, generation or collection process, preprocessing, train/validation/test split, sample scale, scenario coverage, and important paths
- model framework: architecture, key modules, inputs and outputs, major parameters, and why this framework was chosen or changed
- training framework or protocol: scripts, launch commands or entry points, loss functions, optimizer/scheduler if relevant, epochs or stopping criteria, evaluation protocol, checkpoints, and runtime environment
- stage results: metrics, comparisons, qualitative findings, failures, regression signals, and whether the result passes the current gate
- artifact paths: datasets, scripts, configs, checkpoints, logs, TensorBoard runs, result directories, figures, and reports
- remaining risks, open questions, and known limitations
- next phase plan: recommended action, priority, required resources, and concrete next checkpoint

## Derivation Checklist

When deriving a specialized skill from this template, specify:

- the final target the autonomous follow-up mode must keep pursuing
- the new skill name, generated by Codex from the final target when the user did not provide one
- the project name
- the stable paths or artifacts
- the project-specific monitoring cadence
- the project-specific promotion gates
- the project-specific documentation files
- the Chinese follow-up file path and Chinese key phase summary file path
- the user-facing key-results dashboard path
- whether the default tmux Codex worker layer needs any project-specific launch, resource, or consultation-window rules

Make the final target concrete enough that a later session can decide whether to keep monitoring, launch the next run, pivot, or stop. Do not derive a specialized skill whose mission is only "work on this project" without a measurable or inspectable target.
The skill name should be concrete, stable, and target-derived; do not block derivation just because the user did not name the skill.

Keep the derived skill narrow. Do not duplicate this generic workflow unless the project truly needs hard-coded rules.
