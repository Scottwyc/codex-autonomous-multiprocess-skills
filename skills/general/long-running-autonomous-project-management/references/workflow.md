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
   - By default, launch non-blocking branch workers through `tmux-codex-parallel-workers` and keep the current session as coordinator.
   - Skip tmux workers only when the user disables worker parallelism, the environment lacks `tmux` or `codex`, cost/quota constraints make extra Codex processes inappropriate, or there is no independent side branch worth parallelizing.
   - For long-lived work, start the read-only consultation worker so the user can ask status and evidence questions in its tmux window without interrupting the coordinator.
   - For experiment branches, prefer visible `--worker-kind autonomous-experiment` workers so the user can attach to tmux and watch the Codex worker's actual planning, checks, commands, diagnostics, and handoff.
   - When a busy interactive worker needs an immediate redirect, use `interrupt-send`: the manager submits the new message first, then sends `Escape` so Codex switches to the queued instruction.
   - For long-lived tmux Codex operation, start `start-health-supervisor` to recover interactive panes stuck on known transient Codex network/subprocess errors. Include the coordinator pane with `--watch-target main=<SESSION:WINDOW.PANE>` only when the main Codex is itself running inside tmux.
4. Monitor on a cadence.
   - Early launch: check frequently.
   - Stable run: check at a slower cadence.
   - Near decision point: tighten the cadence.
   - Main-coordinator checks must be short and bounded. Use `supervise --once`, `jobs`, `progress`, and short log tails.
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
   - Use a concrete timestamp with timezone on every status or log entry, for example `2026-05-03 21:40:00 CST`.
   - For a dedicated follow-up report, use append-only progress subsections. Each subsection should be one key progress event, and its heading must include a concrete timestamp with timezone, for example `### 2026-05-03 21:40:00 CST Update: validation finished`.
   - Do not let the follow-up file and key phase summary file collapse into one artifact. The follow-up file is the operational timeline; the key phase summary file is the concise restartable conclusion record.
7. Repeat while autonomous follow-up mode is active.
   - Do not voluntarily exit the current session.
   - If the user gives an intermediate instruction, complete it, then resume the overall objective.
   - Stop completely only when the user explicitly says to exit autonomous follow-up mode, such as "退出自主跟进模式" or "exit autonomous follow-up mode".

## Resource Planning Heuristics

- If GPU is free, prefer launching meaningful work rather than leaving the system idle.
- If GPU is full but CPU is spare, use CPU for evaluation, analysis, cleanup, plotting, or documentation.
- If CPU becomes the bottleneck, avoid launching more dataloader-heavy jobs.
- Prefer a few disjoint experiments over many nearly identical ones.
- In autonomous follow-up mode, assume tmux Codex workers are the default route for useful side branches. Give each worker a disjoint write scope and explicit resource ownership before launch.
- Keep `.codex/tmux-workers/COORDINATOR_SCHEDULE.md` and `.codex/tmux-workers/consult/CONSULT_CONTEXT.md` current so worker state, scheduling decisions, and user-consultation answers remain auditable.
- Do not keep the coordinator alive with bare `sleep`, `tail -f`, `watch`, foreground training, or unbounded Python loops. Put those jobs in tmux or background processes with registered PID/log/resource ownership.

## Monitoring Cadence

Use the runtime speed to choose the interval.

- Very fresh launch: minutes
- Short stable job: tens of minutes
- Long training job: longer waits, with sharper checks around key epochs or promotion gates

During each check, verify:

- process exists
- logs are updating
- metrics are sensible
- no duplicate or zombie jobs have appeared
- tmux Codex workers are still relevant, and their captured output has been reviewed before any integration decision
- the dedicated consultation worker has refreshed context after major scheduling decisions, if it is running

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
- whether the default tmux Codex worker layer needs any project-specific launch, resource, or consultation-window rules

Make the final target concrete enough that a later session can decide whether to keep monitoring, launch the next run, pivot, or stop. Do not derive a specialized skill whose mission is only "work on this project" without a measurable or inspectable target.
The skill name should be concrete, stable, and target-derived; do not block derivation just because the user did not name the skill.

Keep the derived skill narrow. Do not duplicate this generic workflow unless the project truly needs hard-coded rules.
