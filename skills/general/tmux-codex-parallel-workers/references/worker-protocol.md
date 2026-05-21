# Worker Protocol

## Task Shape

Give every worker a bounded task that can run without blocking the coordinator's next step.

Include:

- objective
- working directory
- read/write scope
- expected output
- commands or resources it may use
- what it must not touch
- how to report completion
- progress file path and report file path
- inbox directory
- owned paths and resource tokens when edits or hardware are involved
- background job registry path
- git worktree branch/path when isolation is enabled
- model and reasoning effort

## Prompt Template

```text
You are Worker <name> in a tmux-launched Codex process.
Goal: <one concrete result>
Working directory: <path>
Write scope: <paths/modules>
Do not modify: <paths/modules>
Run or inspect: <commands/logs/files>
Return: changed files, commands run, result, blockers, next recommendation.
Progress file: <state-dir>/progress/<name>.md
Report file: <state-dir>/reports/<name>.md
Inbox directory: <state-dir>/inbox/<name>/
Owned paths: <absolute paths or project-relative paths assigned by coordinator>
Resources: <gpu:N, port:N, out:path, cpu:label, etc.>
Job registry: <state-dir>/jobs/<name>.json
Git worktree: <path and branch, when enabled>
Model: <model, default gpt-5.5>
Reasoning effort: <effort, default xhigh>
```

## Coordination Rules

- Use one worker per independent branch of work.
- Avoid overlapping write scopes. If overlap is unavoidable, keep one worker read-only.
- Keep the coordinator responsible for final merge, judgment, and user-facing conclusions.
- Keep `COORDINATOR_SCHEDULE.md` current; users should be able to audit worker purpose, resource ownership, results, and next decisions from that single document.
- Record each worker's model and reasoning effort in the schedule; default to the manager's strongest available setting unless cost/quota/speed requires an explicit override.
- Execution workers run with the worker state directory mounted through Codex CLI `--add-dir` when using `workspace-write`. This prevents git-worktree workers from treating `.codex/tmux-workers/progress` and `.codex/tmux-workers/reports` as read-only because those files live outside the worker `--cd` root.
- Prefer `codex exec` workers for unattended one-shot tasks.
- Use interactive workers when the coordinator expects follow-up input or when the user should be able to watch the Codex worker's planning, inspections, command launches, and recovery decisions in tmux.
- Use `--worker-kind autonomous-experiment` for visible, independently progressing experiment branches.
- Capture output before making decisions from a worker's result.
- Record worker launches and decisions in the project status document for long-running autonomous work.
- Send multiline prompts through paste-buffer, not raw `send-keys`.
- For long or important instructions, write an inbox file and paste a short read-this-file instruction.
- When interrupting a busy interactive worker, paste and submit the new prompt first, then send Escape so Codex switches to the queued instruction immediately.
- If a supervisor asks a progress question, it must send a continue prompt after capturing the answer.
- Treat `workers.json` and `status/<worker>.json` as the manager-owned registry; workers may read them but should not edit them.
- Treat `COORDINATOR_SCHEDULE.md`, `schedule_events.jsonl`, and `consult/` as manager-owned artifacts; workers may read them, but should update state through progress/report files or manager commands.
- Register long-running background processes with `job-add` immediately after launch.
- When running inside a git worktree, keep changes in that worktree and report the diff summary before completion.
- The coordinator should use `schedule-note` for non-obvious decisions: launching workers, changing resources, accepting/rejecting results, recovering stalled work, or stopping jobs.
- For long-lived autonomous runs, keep a dedicated consultation worker running with `start-consult` so users can ask status and evidence questions without interrupting the coordinator.
- Run persistent supervisor loops only through `start-supervisor` in tmux. Coordinator-side checks should use `supervise --once`.
- The supervisor may query interactive workers only when explicitly enabled; by default those queries are restricted to workers already marked `stalled`.
- For long-lived autonomous runs, start `start-health-supervisor` as a separate tmux health loop. It watches interactive Codex panes for recoverable network/subprocess errors and resumes only after stability/cooldown checks.
- Add the main coordinator pane with `--watch-target main=<SESSION:WINDOW.PANE>` only when the coordinator is itself running inside tmux and the user wants it auto-recovered. Use `--observe-target` for panes that must never receive recovery prompts.

## Consultation Worker Rules

The consultation worker is a separate read-only Codex process, not an execution worker.

It should:

- read `<state-dir>/consult/CONSULT_CONTEXT.md` and `<state-dir>/COORDINATOR_SCHEDULE.md` before every answer
- answer questions about the current mission, worker tasks, scheduling decisions, resources, logs, reports, evidence paths, blockers, and next checkpoints
- default to Chinese unless the user asks otherwise
- give concrete file paths and manager commands for user audit
- state missing evidence clearly instead of guessing

It must not:

- start, stop, resume, launch, interrupt, or supervise workers
- edit project files, schedule files, registry files, reports, source code, or artifacts
- make final promotion, merge, or integration decisions
- mutate state when a user asks for an action; it should redirect execution requests to the coordinator or manager commands

## Resource Rules

- Do not launch workers that compete for the same GPU, port, result directory, checkpoint path, or database migration unless the resource split is explicit.
- For GPU experiments, assign device IDs or scheduler constraints in the worker prompt and launch command.
- For autonomous experiment workers, prefer visible `interactive` mode. The worker should keep the tmux Codex pane useful for review by briefly stating major intent before important actions and by running short inspections visibly.
- For repository edits, ask each worker to list changed files and never revert unrelated changes.
- Stop stale or duplicate workers before launching replacements.
- Use manager-level `--owned-path` and `--resource` declarations so conflicts are visible before launch.
- For overlapping source-code changes, prefer a future git-worktree workflow or keep one worker read-only.
- For long jobs, record PID, command, log path, and resource tokens in `jobs/<worker>.json` through the manager.
- Do not keep long-running training, `tail -f`, `watch`, or monitor loops in a worker's foreground Codex command. Start them as background jobs, register them with `job-add`, and continue with short bounded inspections.

## Autonomous Experiment Worker

An autonomous experiment worker is a visible interactive worker that owns a bounded experiment branch.

It may:

- inspect current code, data, configs, logs, and metrics
- launch training/evaluation/data jobs inside assigned resources
- diagnose failed runs and apply limited fixes inside assigned write scope
- iterate until it reaches a useful report, blocker, or coordinator checkpoint

It must:

- keep the tmux Codex pane readable as an operation trace
- register long-running jobs with `job-add`
- update progress after launches, failures, metric checkpoints, and handoff
- write a final report with commands, logs, metrics, artifacts, failed attempts, changed files, and next recommendation
- avoid final merge/promotion decisions

## Progress File Format

Workers should keep the generated progress file restartable:

```markdown
# <worker> Progress

Updated: <timestamp>
Status: running | waiting | blocked | completed

## Current Progress

- Completed: ...
- Running: ...
- Next: ...

## Evidence

- Logs: ...
- Results: ...
- Changed files: ...
```

Use the report file for longer summaries, metric tables, and final conclusions.

## Completion Requirements

Before exiting, a worker should:

- update the progress file with `Status: completed` or `Status: blocked`
- write a final report if the result is longer than a short terminal answer
- list changed files and commands run
- state whether any background job remains active and record its PID/log path
- summarize git changes if working in a git worktree
- avoid closing or deleting artifacts needed by the coordinator
