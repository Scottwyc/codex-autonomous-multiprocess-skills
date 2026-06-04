# User-Facing Key Results Dashboard

## Purpose

Maintain one stable, concise navigation document that lets a user answer:

- What are the project targets?
- Which targets are complete, boundary-complete, active, blocked, or pending?
- What are the latest accepted conclusions and metrics?
- Where are the readable reports, canonical result directories, and figures?
- What is currently running, and what is the next decision gate?

This dashboard is not a worker registry, schedule transcript, or experiment log.

## Default Path

Prefer a project-owned tracked path:

`docs/<project>-key-results-dashboard.md`

Do not put the primary user dashboard under `.codex/`; `.codex` is the control plane and may be compacted or ignored by Git.

## Status Vocabulary

Use a small explicit vocabulary:

- `已完成`: the defined target gate is closed with accepted evidence.
- `边界完成`: a negative, fail-closed, or empirical-boundary result is accepted as the target's intended conclusion.
- `进行中`: an authorized owner is actively advancing the target.
- `受阻/关闭`: the current route is fail-closed or blocked; state the reopening condition.
- `待推进`: incomplete with no active owner.
- `已取代`: superseded evidence retained only for history.

Never mark a target complete because its report was written, a quick subset passed, or a worker stopped.

## Required Sections

### 1. Header

Include:

- exact update timestamp with timezone
- project mission
- authoritative status source
- status legend

### 2. Target Overview

Use one row per target:

| Target | Status | Latest accepted conclusion | Key result documents | Result directory | Active progress | Next gate |
|---|---|---|---|---|---|---|

Rules:

- Keep conclusions to one or two sentences.
- Link readable reports first.
- Link canonical result directories after reports.
- Link live progress only for active work.
- Use `无`, `待生成`, or `未授权` instead of broken links.

### 3. Active Work

Use one row per active branch, not one row per process:

| Target/branch | Owner | Current milestone | Expected output | Next meaningful check |
|---|---|---|---|---|

Keep raw PIDs, repeated watcher observations, and unchanged heartbeats out of this table.

### 4. Key Result Index

Group direct links by:

- external-reader reports
- phase/closure reports
- canonical result directories
- result figures or visualization directories

### 5. Blockers And Claim Boundaries

State the smallest evidence that would change each incomplete or boundary-complete status. Explicitly separate deployable model results from oracle, post-hoc, diagnostic, quick, and documentation-only evidence.

## Link Rules

- Prefer relative Markdown links so the dashboard works in the repository and GitHub.
- Link directly to files or directories, not only to a parent repository.
- Use descriptive labels such as `Target10 OOD report`, not raw paths as link text.
- Do not link temporary logs when a stable report or result root exists.
- Do not create links to expected future artifacts before they exist.

Validate local links:

```bash
python /path/to/long-running-autonomous-project-management/scripts/validate_user_results_dashboard.py \
  docs/<project>-key-results-dashboard.md \
  --require-section "目标总览" \
  --require-section "正在进行" \
  --require-section "关键结果索引"
```

## Update Triggers

Refresh the dashboard when any of these materially changes:

- target status or accepted conclusion
- key metric or comparison
- readable result document, result directory, or figure set
- active owner or branch
- blocker, reopening condition, or next gate

Do not refresh it for unchanged polls, supervisor captures, log growth, or internal worker messages that do not alter user-visible state.

## Compact Template

```markdown
# <Project> 关键结果看板

- 更新时间：<timestamp with timezone>
- 项目目标：<mission>
- 状态依据：[权威状态文档](relative/path.md)
- 状态：已完成 / 边界完成 / 进行中 / 受阻或关闭 / 待推进

## 目标总览

| Target | 状态 | 最新结论 | 关键结果文档 | 关键结果目录 | 正在进行 | 下一 gate |
|---|---|---|---|---|---|---|
| T1 | 已完成 | <accepted conclusion> | [详细报告](report.md) | [结果目录](../results/t1/) | 无 | 仅在新证据出现时刷新 |

## 正在进行

| Target/分支 | Owner | 当前进展 | 预期产物 | 下一关键检查 |
|---|---|---|---|---|

## 关键结果索引

### 面向外部读者的报告

- [Target 1 详细报告](target1-report.md)

### 核心结果目录

- [Target 1 canonical results](../results/t1/)

## 阻塞与结论边界

- Target X：<why incomplete; exact reopening gate>
```
