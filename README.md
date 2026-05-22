# Codex Autonomous Multiprocess Skills

This repository packages two Codex skills for long-running autonomous work with tmux-managed Codex worker processes.

## Included Skills

- `long-running-autonomous-project-management`
  - Generic long-running autonomous project management workflow.
  - Defaults to tmux-launched Codex workers for useful non-blocking branch tasks.
  - Uses subordinate branch-manager workers for major experimental branches when direct coordinator tracking would be too heavy.
  - Defines coordinator responsibilities, monitoring cadence, documentation discipline, and failure handling.

- `tmux-codex-parallel-workers`
  - Launches, supervises, health-monitors, interrupts, resumes, and stops independent Codex CLI workers in separate tmux sessions.
  - Supports visible `autonomous-experiment` workers, subordinate `branch-manager` workers, manager-mediated `peer-send` worker messages, read-only consultation workers, coordinator constraints, context packs, compact memory, scheduling docs, coordinator recovery handoffs, worker progress/report files, background job registries, optional git worktrees, health recovery for transient Codex pane errors, and main-coordinator restart after context-window exhaustion.

Together, the two skills form an autonomous multiprocess management framework: a main Codex coordinator keeps final judgment and integration authority, while separate tmux Codex workers execute branch tasks in parallel.

The framework also treats the coordinator context window as a limited resource. Worker progress, reports, schedule docs, consultation answers, and supervisor captures are designed to summarize first and point to files for long logs, full diffs, large tables, and tmux transcripts.

For large experiment lines, the coordinator can delegate branch-level planning to a `branch-manager` worker. That branch manager can launch front-line `autonomous-experiment` children with `--parent-worker`, coordinate short `peer-send` messages between them, and report branch-level summaries back to the main coordinator.

If the main coordinator itself is running inside tmux, it can be registered with `register-coordinator`. The health supervisor can then detect `Codex ran out of room in the model's context window` or a missing registered coordinator target, close the exhausted coordinator pane when present, launch a recovered coordinator in its own tmux session with `recover-coordinator`, and hand it `COORDINATOR_RECOVERY.md` plus the existing worker registry, schedule, progress/report files, jobs, peer messages, branch summaries, and consultation context.

## Documentation Maintenance / README 维护规则

Important repository changes must update this README in the same commit. This includes user-facing skill behavior, manager commands or flags, worker kinds, scheduling defaults, safety rules, bundled scripts, state files, and operational constraints.

README updates should name the feature, explain why it matters, and include a minimal command or workflow example when applicable. Do not leave important new functionality documented only inside skill internals or scripts.

仓库后续每次加入重要功能、修改默认行为、扩展脚本命令、调整安全边界或改变调度流程时，都必须同步更新本 README，并说明新功能的用途、使用方式和必要示例。

## 中文框架说明

### 1. 目标

这套框架用于让一个主 Codex 在长时间自主模式下稳定运行，并通过 tmux 统一调度多个独立的子 Codex 进程协作完成任务。

核心目标是：

- 主进程长期在线，负责拆解任务、资源规划、最终判断和结果合并。
- 子 Codex 在独立 tmux session 中运行，负责支线执行、实验巡检、报告草拟、代码修改或数据分析。
- 对重大实验分支，主进程可以启动下属 `branch-manager` worker，由它继续调度一线 `autonomous-experiment` worker。
- 一线 worker 之间可以通过 manager 记录的 `peer-send` 消息传递证据、阻塞点和 artifact 路径，但不能私自改变任务边界或资源分配。
- 所有 worker 都有可恢复的本地状态：任务计划、进展、报告、日志、inbox、status、后台 job registry。
- 主进程可以定期监督、发送指令、暂停/恢复 worker，并在最后收集所有证据。

### 2. 架构

```text
主 Codex / Coordinator
  |
  |-- codex_tmux_manager.py
  |     |
  |     |-- tmux namespace: cw
  |     |     |-- session: cw-worker-a           window: codex
  |     |     |-- session: cw-worker-b           window: codex
  |     |     |-- session: cw-consult            window: consult
  |     |     |-- session: cw-supervisor         window: supervisor
  |     |     |-- session: cw-health-supervisor  window: health-supervisor
  |     |     |-- session: cw-main-recovered-... window: codex
  |     |
  |     |-- .codex/tmux-workers/
  |           |-- workers.json             -> worker registry
  |           |-- COORDINATOR_CONSTRAINTS.md -> 所有子进程优先加载的统一约束
  |           |-- COORDINATOR_CONTEXT_PACK.md -> 主进程最短上下文包
  |           |-- COORDINATOR_MEMORY.md    -> 主进程压缩工作记忆
  |           |-- COORDINATOR_SCHEDULE.md  -> 主进程调度总览文档
  |           |-- COORDINATOR_RECOVERY.md  -> 主进程重启接管 handoff
  |           |-- coordinator_constraints_events.jsonl -> 统一约束事件
  |           |-- coordinator_memory_events.jsonl -> 压缩记忆事件
  |           |-- schedule_events.jsonl     -> 调度事件日志
  |           |-- peer_messages.jsonl       -> worker 横向消息日志
  |           |-- consult/
  |           |     |-- CONSULT_CONTEXT.md   -> 用户咨询窗口上下文快照
  |           |     |-- consult.prompt.md    -> 咨询 worker 启动规则
  |           |     |-- consult.status.json  -> 咨询 worker 状态
  |           |-- manager.log              -> coordinator operation log
  |           |-- tasks/                   -> launch/resume prompts
  |           |-- workplans/               -> worker task plans
  |           |-- progress/                -> worker progress files
  |           |-- reports/                 -> worker reports and collection summaries
  |           |-- inbox/<worker>/          -> auditable coordinator messages
  |           |-- status/<worker>.json     -> running/completed/failed/stalled state
  |           |-- status/health_supervisor.json -> 健康守护状态
  |           |-- status/health_supervisor_state.json -> 健康守护循环记忆/cooldown
  |           |-- jobs/<worker>.json       -> background PIDs launched by worker
  |           |-- captures/<worker>/       -> supervisor captures
  |           |-- git-worktrees/<worker>/  -> optional isolated git worktrees
```

tmux 组织规则：

- `--session cw` 默认只是命名空间前缀，不再是所有 worker 共用的 tmux session。
- 每个 worker、consult、supervisor、recovered main coordinator 默认都有自己的独立 tmux session，方便用户在多个终端里同时 attach 查看。
- 例如 `launch exp-a` 默认得到 `cw-exp-a:codex`，而不是 `cw:cw-exp-a`。
- 查看全部相关 session：`tmux ls | rg '^cw(-|:)'`。
- 进入单个 worker：`tmux attach -t cw-exp-a`。
- 只有显式传 `--shared-session` 时，才使用旧模式：一个 `cw` session 下面多个 `cw-*` window。

主进程只把适合并行的支线拆给 worker。任何最终合并、实验结论、代码接受、指标判断，都必须由主进程复核。

对于复杂实验线，推荐层级化：

```text
主 Codex / Coordinator
  |
  |-- branch-manager worker: 重大实验分支 A
  |     |-- autonomous-experiment worker A1
  |     |-- autonomous-experiment worker A2
  |     |-- peer-send: A1 -> A2 的短证据/依赖消息
  |
  |-- branch-manager worker: 重大实验分支 B
        |-- autonomous-experiment worker B1
```

主进程主要审查 branch manager 的分支级汇总；只有失败、合并、资源冲突、用户审计或最终结论需要时，才深入一线 worker 的完整日志和产物。

#### 2.1 默认模型策略

这套框架默认让 tmux Codex worker 使用当前环境中的“最新、最强、最深思考”配置。

截至本机当前配置：

```text
model = gpt-5.5
model_reasoning_effort = xhigh
```

manager 在启动 worker 时默认传入：

```bash
--model gpt-5.5 -c model_reasoning_effort=\"xhigh\"
```

这样 worker 默认不会因为独立 tmux 进程而降级到较弱模型。

全局覆盖：

```bash
export CODEX_WORKER_DEFAULT_MODEL=gpt-5.5
export CODEX_WORKER_DEFAULT_REASONING=xhigh
```

单个 worker 覆盖：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  launch fast-audit \
  --model gpt-5.4 \
  --reasoning-effort high \
  --task "快速审计日志并报告异常。"
```

如果希望完全使用 Codex CLI 或 profile 自己的默认模型：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  launch profile-worker \
  --no-best-model \
  --profile default \
  --task "按 profile 默认设置运行。"
```

调度文档会记录每个 worker 实际使用的 `model` 和 `reasoning effort`，方便用户审查是否按预期使用了强模型。

#### 2.2 专用用户咨询窗口

长程自主任务中，用户经常只想询问“现在有哪些 worker、谁在跑什么、证据在哪里、下一步是什么”，不一定要打断主进程。框架因此提供一个常驻的只读咨询 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-consult --cwd "$PWD"
```

默认位置：

```text
tmux: cw-consult:consult
context: .codex/tmux-workers/consult/CONSULT_CONTEXT.md
schedule: .codex/tmux-workers/COORDINATOR_SCHEDULE.md
log: .codex/tmux-workers/logs/consult.log
```

咨询 worker 默认使用强模型配置，但 sandbox 为 `read-only`。它的职责是回答用户关于当前全局任务、worker 状态、日志、结果、资源、阻塞点、证据路径的问题。它不负责启动、停止、恢复、修改文件、合并代码或做最终决策。

主进程每次刷新调度文档时，manager 会同步刷新：

```text
.codex/tmux-workers/consult/CONSULT_CONTEXT.md
```

如果希望主动通知咨询窗口重新读取上下文：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  consult-sync --notify --message "主进程已完成新一轮 worker report 审查。"
```

用户可以 `tmux attach -t cw-consult` 直接进入咨询窗口提问。这样主进程可以继续保持长程自主推进，不必因为状态咨询被频繁打断。

#### 2.3 主进程上下文预算

多 worker 并行时，最大的隐性成本是主进程上下文窗口被 worker 输出、日志、长表格和 tmux scrollback 占满。框架的默认通信原则是：

- 主进程只消费摘要、证据路径、阻塞点和下一步，不消费完整日志流。
- `COORDINATOR_CONTEXT_PACK.md` 是最短重载包，`COORDINATOR_MEMORY.md` 是主进程压缩工作记忆，`COORDINATOR_SCHEDULE.md` 是审计型调度总览，`CONSULT_CONTEXT.md` 是用户咨询上下文；它们都不是 tmux transcript 镜像。
- worker 的 progress 文件保持短小、可恢复，通常不超过 10 条要点：状态、最新结果、证据路径、阻塞点、下一步。
- report 文件保存较完整但仍经过整理的结论；原始日志、完整表格、完整 diff、大段输出应写入独立 artifact/log 文件，并在 report 中引用路径。
- interactive/autonomous-experiment worker 的 tmux pane 应展示“意图、短命令、短 tail/指标、判断、下一步”，不要用整屏训练日志或大表格刷屏。
- 主进程巡检优先使用 `compact-memory --print --context-pack`、`compact-memory --print`、`list`、`jobs`、`progress --lines 20`；只有短记忆不足时才读 `schedule`、`collect --lines 20/30`、扩大 `capture --lines` 或读取完整 artifact。
- 主进程做出重要判断后，应运行 `compact-memory --note ... --decision ... --next-action ...`，把聊天上下文里的关键状态压缩进文件。
- 咨询 worker 默认也应简洁回答，必要时给出文件路径和命令，让用户自己追溯完整证据。

#### 2.3.1 统一运行约束

主进程应维护：

```text
.codex/tmux-workers/COORDINATOR_CONSTRAINTS.md
```

这是所有 tmux Codex 子进程必须优先加载的统一约束文件，优先级高于单个 worker 的任务 prompt。它用于记录跨 worker 的资源、安全和运行规则，例如：

- TensorBoard/dashboard 安全端口范围；
- dashboard 默认只绑定 `127.0.0.1`；
- GPU、端口、输出目录、checkpoint、数据集的所有权；
- remote job 的 host/session/log/liveness 记录要求；
- 禁止无授权 `rm -rf`、删除 checkpoint、杀无关进程；
- 共享数据、缓存和结果目录的不可覆盖规则。

查看或初始化默认约束：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  constraints --print
```

设置 TensorBoard 安全端口范围：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  constraints --tensorboard-port-range 16006-16099
```

追加项目级约束：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  constraints --append "所有 TensorBoard 必须绑定 127.0.0.1，并用 --resource port:<PORT> 记录端口所有权。"
```

manager 会把这个约束文件写进 worker plan、worker prompt、resume prompt、branch-manager prompt、consult prompt 和 recovered coordinator prompt。worker 启动时会先收到“先读统一约束，再读任务 prompt”的指令。

推荐的信息流是：

```text
raw logs / full tables / full diffs / tmux transcript
  -> artifact/log/capture 文件
  -> worker report 的压缩结论和路径
  -> progress 的 5-10 行状态
  -> COORDINATOR_CONTEXT_PACK.md 的最短重载包
  -> COORDINATOR_MEMORY.md 的压缩工作记忆
  -> schedule/consult context 的审计摘要
  -> 主进程最终判断
```

#### 2.4 下属管理 worker 与横向通信

`branch-manager` worker 是主进程下属的分支管理者，适合用于重大实验分支。主进程只给它明确的分支目标、资源边界、写入范围和预期分支报告；它负责继续拆解一线任务、启动子 worker、协调横向消息、汇总分支结果。

使用边界：

- 主进程负责全局目标、跨分支资源、最终合并、最终指标判断和对用户汇报。
- branch manager 负责单个重大分支内部的任务拆分、子 worker 调度、子结果初步整合和分支级报告。
- 一线 autonomous-experiment worker 负责具体实验、巡检、诊断、日志/指标产物和自己的 progress/report。
- worker 横向通信必须通过 `peer-send`，写入 `peer_messages.jsonl` 和目标 worker inbox，不能私下改调度状态。

`peer-send` 适合传递：

- 某个实验产物路径
- 某个配置/指标/错误片段
- “请在下一次安全 checkpoint 检查这个 artifact”
- 依赖关系或阻塞点

`peer-send` 不适合传递：

- 改变另一个 worker 的写入范围
- 改变 GPU/端口/输出目录资源分配
- 宣布最终结论或通过 gate
- 粘贴长日志、完整 diff、大表格

### 3. 基本初始化

在项目根目录执行：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  init --cwd "$PWD" \
  --mission "本轮长程自主任务目标"
```

这会初始化项目本地状态目录。默认独立 session 模式下，`cw` 是命名空间前缀；worker 会在后续 launch 时创建自己的 session。

初始化后会生成：

```text
.codex/tmux-workers/COORDINATOR_CONSTRAINTS.md
.codex/tmux-workers/COORDINATOR_CONTEXT_PACK.md
.codex/tmux-workers/COORDINATOR_MEMORY.md
.codex/tmux-workers/COORDINATOR_SCHEDULE.md
.codex/tmux-workers/COORDINATOR_RECOVERY.md
```

这是主进程维护的调度总览文档，用户可以直接审查它来理解当前有哪些 worker、为什么启动、各自任务是什么、资源怎么分配、结果在哪里、下一步是什么。

如果主 Codex 本身也运行在 tmux 中，建议注册主进程 target，方便意外挂掉或上下文耗尽后由新主进程接管：

```bash
tmux display-message -p '#S:#W.#{pane_index}'

python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  register-coordinator \
  --target <SESSION:WINDOW.PANE> \
  --cwd "$PWD" \
  --mission "本轮长程自主任务目标"
```

注册后，manager 会在 `workers.json` 中记录主进程 target、cwd、模型、恢复窗口前缀和恢复参数，并持续维护 `COORDINATOR_RECOVERY.md`。这个 handoff 文件是新主进程的接管入口，包含当前目标、worker 总览、关键文件、最近调度事件、peer messages 和恢复后的第一批检查命令。

手动压缩主进程记忆：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  compact-memory \
  --note "当前分支 manager 已完成子 worker 分工。" \
  --decision "暂不新增 worker，等待 gpu:0 任务产出指标。" \
  --next-action "下一轮先看 compact-memory、jobs、progress --lines 20。"
```

查看最短上下文包：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  compact-memory --print --context-pack
```

注意：普通执行 worker 使用 `workspace-write` sandbox 时，manager 会额外给 Codex CLI 传入：

```bash
--add-dir <state-dir>
```

这样即使 worker 运行在 `--git-worktree` 创建的独立工作树里，也能写入 `.codex/tmux-workers/progress/` 和 `.codex/tmux-workers/reports/`。没有这个挂载时，worktree worker 的 `--cd` 根目录是 `.codex/tmux-workers/git-worktrees/<worker>/`，而 progress/report 在它的父级目录，容易被 Codex 视为只读或工作区外路径。

worker 只能按约定更新 progress/report，或通过 manager 命令如 `job-add` 更新状态；不要手工编辑 `workers.json`、`status/`、`COORDINATOR_SCHEDULE.md` 或调度事件。

对于长程自主跟进，建议初始化后立即启动咨询窗口：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-consult --cwd "$PWD"
```

#### 3.1 主进程调度文档

调度文档是这套框架的核心审查入口。它由 manager 自动刷新，并由主进程通过 `schedule-note` 补充人为决策。

查看调度文档路径：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  schedule
```

直接打印内容：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  schedule --print
```

记录调度决策：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  schedule-note \
  --event decision \
  --worker long-eval \
  --decision "继续保留 long-eval，占用 gpu:0；当前指标未完整产出。" \
  --next-action "下一次 supervisor capture 后检查 jobs 和 report。"
```

更新总目标：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  schedule-note \
  --mission "验证新模型在真实数据和合成数据上的跨域稳定性"
```

调度文档包含：

- 当前总目标
- 用户审查入口命令
- worker 总表
- 每个 worker 的状态、任务、类型、上级 worker、模型、reasoning effort、资源、owned paths、tmux 位置
- workplan/progress/report/inbox/jobs/status 文件路径
- 后台 job 当前 PID 状态
- worker 横向消息摘录
- progress/report 最新摘录
- git worktree 和 diff 摘要
- 调度事件日志
- 主进程审查清单

主进程必须在以下场景写 `schedule-note`：

- 新增或停止 worker
- 改变资源分配
- 发现 worker stalled/failed 后决定恢复或放弃
- 接受或拒绝 worker 结果
- 暂停、停止或保留后台 job
- 准备向用户汇报阶段性结果
- 进入下一阶段实验或合并代码前

### 4. 启动 worker

#### 4.1 一次性 exec worker

适合日志审计、结果汇总、只读分析、短脚本生成。

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
  --task "Inspect current experiment outputs and report complete metrics, missing files, and next evaluation."
```

#### 4.2 interactive worker

适合长时间任务、需要后续指令的任务、需要 supervisor 查询的任务，以及用户希望直接在 tmux 里看到 Codex worker 操作过程的任务。interactive worker 默认使用 Codex 标准 alternate-screen TUI，这能稳定保留底部输入栏、`working` 状态行和完整交互体验。

如果确实需要 inline scrollback，可以显式加 `--inline-tui`，manager 才会向 Codex 传入 `--no-alt-screen`。不要把 inline TUI 作为可观察 worker 的默认模式，因为它可能导致底部 prompt/status 行显示异常。

如果某个旧 worker 已经以 inline TUI 启动，并出现底部提示词、输入栏或 `working` 状态行消失，按下面方式重开该 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  stop <worker>

python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  resume <worker> --mode interactive
```

如果是主进程窗口本身需要重开，应先确保已经 `register-coordinator`，再用 `recover-coordinator --kill-old` 从 durable state 开一个新的主进程窗口。

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch long-eval \
  --cwd "$PWD" \
  --mode interactive \
  --write-scope "results/long-eval/, docs/long-eval-report.md" \
  --owned-path "results/long-eval/" \
  --owned-path "docs/long-eval-report.md" \
  --resource "gpu:0" \
  --task "Run the evaluation plan, update progress, and write a final report." \
  --start-supervisor \
  --supervisor-interval 300 \
  --supervisor-query-interval 1800 \
  --query-interactive
```

interactive worker 的输出会通过 `tmux pipe-pane` 写入 log，避免 `tee` 管道破坏 Codex TUI。

#### 4.3 visible autonomous experiment worker

如果希望 worker 不只是做日志调查，而是独立推进实验支线，并且用户可以在 tmux 中看到 Codex worker 的具体运作过程，使用：

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

`autonomous-experiment` 默认：

- 使用 `interactive` Codex TUI，而不是 `codex exec`。
- 启动后在 worker 自己的 tmux session 中粘贴任务 prompt，例如 `cw-exp-a:codex`。
- 自动启动独立 supervisor session，例如 `cw-supervisor:supervisor`，除非显式加 `--no-start-supervisor`。
- 要求 worker 在可见 tmux pane 中说明关键计划、短命令检查、失败诊断、实验启动和阶段性判断。
- 长训练/评估仍应作为后台 job 运行并通过 `job-add` 注册 PID/log/resource；Codex worker pane 负责展示“为什么跑、如何诊断、下一步是什么”，而不是只展示训练日志。
- 噪声大的输出应重定向到 log 文件；worker 只在 pane 中读取短 tail、关键指标或错误片段。

用户查看方式：

```bash
tmux ls | rg '^cw(-|:)'
tmux attach -t cw-exp-a
# 如果已经在 tmux 内：
tmux switch-client -t cw-exp-a:codex
```

manager 默认创建独立 tmux session，所以不同 worker 可以在不同终端里同时 attach 查看。只有显式传 `--shared-session` 时，才回到旧的 `cw:cw-exp-a` 多 window 模式。

#### 4.4 branch-manager worker

当一个重大实验分支内部还需要多个一线 worker 并行推进时，主进程优先启动一个 `branch-manager`：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch moe-branch-manager \
  --cwd "$PWD" \
  --worker-kind branch-manager \
  --manager-scope "协调 MoE 分支；可在 results/moe-branch/ 和 docs/moe-branch/ 下启动子 worker 并汇总结果。" \
  --owned-path "results/moe-branch/" \
  --owned-path "docs/moe-branch/" \
  --resource "gpu:0-1" \
  --write-scope "规划并协调 MoE 分支的一线 autonomous-experiment worker，向主进程提交分支级汇总。" \
  --task "制定分支计划，启动带 --parent-worker moe-branch-manager 的子实验 worker，协调 peer-send 消息，并维护分支级 report。"
```

branch manager 再启动一线 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch moe-k-sweep \
  --parent-worker moe-branch-manager \
  --worker-kind autonomous-experiment \
  --owned-path "results/moe-branch/k-sweep/" \
  --resource "gpu:0" \
  --task "运行 K sweep 子实验，更新 progress，并把指标和 artifact 路径报告给 branch manager。"
```

branch manager 的职责不是替代主进程做最终判断，而是减少主进程直接跟进一线 worker 的压力。主进程通常先看 branch manager 的 report，再决定是否深入检查子 worker。

### 5. Git Worktree 隔离

多个 worker 同时改代码时，推荐使用独立 git worktree：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch fix-loader \
  --cwd "$PWD" \
  --git-worktree \
  --base-ref HEAD \
  --mode exec \
  --owned-path "src/data_loader.py" \
  --task "Fix the loader edge case and run focused tests."
```

默认会创建：

```text
.codex/tmux-workers/git-worktrees/fix-loader/
branch: codex-worker/fix-loader
```

注意：

- worktree 用于隔离 worker 的代码修改。
- 主进程仍需要最终 review diff、运行测试、决定是否合并。
- 如果多个 worktree 修改同一个逻辑文件，最终 merge 仍可能冲突。

### 6. 通信机制

#### 6.1 短指令

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  send long-eval "Please summarize current progress in the progress file."
```

发送使用 `tmux set-buffer` + `tmux paste-buffer` + Enter，适合多行 prompt。

#### 6.2 长指令或重要指令

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  send long-eval --via-inbox --message-file /tmp/next_instruction.md
```

`--via-inbox` 会把完整指令写入：

```text
.codex/tmux-workers/inbox/long-eval/<timestamp>.md
```

然后只向 worker 粘贴一条短消息，让它读取该文件。这样可以审计，也避免长 prompt 在终端中丢失。

#### 6.3 打断 busy worker

如果 worker 正在前台执行命令，需要立刻切换到新指令，使用 `interrupt-send`：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  interrupt-send long-eval "Pause current work, save progress, and report state."
```

注意按键顺序：`interrupt-send` 会先通过 tmux paste-buffer 粘贴并回车提交新消息，再发送 `Escape`，让 Codex TUI 从当前运行切到刚提交的新指令。不要手工改成“先 Escape 再发消息”，否则 busy worker 容易只是取消当前输入或把新消息排队到下一次工具调用之后。

#### 6.4 worker 横向消息

一线 worker 或 branch manager 需要把短证据发给另一个 worker 时，使用 `peer-send`：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  peer-send moe-k-sweep moe-router-audit \
  --message "K sweep 产物在 results/moe-branch/k-sweep/best_config.yaml，请下一轮 router audit 使用。" \
  --notify
```

`peer-send` 会：

- 写入目标 worker 的 inbox；
- 追加 `peer_messages.jsonl`；
- 在来源和目标 progress 中追加短记录；
- 如果加 `--notify`，向目标 tmux pane 粘贴一条“读取 inbox”的短通知。

不要用 `peer-send` 传长日志或完整 diff。涉及资源、scope、最终结论的变化，必须由 branch manager 或主进程通过 `schedule-note` 记录。

### 7. Supervisor

启动 supervisor：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  start-supervisor \
  --interval 300
```

不要在主进程里直接运行无界 `supervise`。现在 `supervise` 默认只允许 `--once` 前台检查；无界循环必须通过 `start-supervisor` 进入 tmux 的 `cw-supervisor:supervisor` 独立 session，避免主 Codex 触发 unified exec warning。

supervisor 做这些事：

- 定期 capture worker tmux 输出。
- 写入 `captures/<worker>/latest.txt` 和时间戳 capture。
- 更新 `status/<worker>.json`。
- 写入 `status/supervisor.json`，记录 supervisor PID、cycle、interval、last loop 时间。
- 按节流参数刷新 `COORDINATOR_SCHEDULE.md` 和 `consult/CONSULT_CONTEXT.md`，默认 900 秒一次。
- 对 progress 文件的未变化 capture 追加也做节流，默认 1800 秒一次，避免长期膨胀。
- 如果启用 `--query-interactive`，默认只询问已经被标记为 `stalled` 的 interactive worker；只有显式使用 `--query-any-running` 才会询问仍在 running 的 worker。

如果同一段输出长时间不变，worker 会被标记为 `stalled`。默认阈值是 1800 秒，可以用：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  supervise --once --stall-seconds 3600
```

常驻 supervisor 的安全参数示例：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  start-supervisor \
  --interval 300 \
  --refresh-schedule-interval 900 \
  --progress-append-interval 1800
```

### 8. Health Supervisor：错误卡死自动恢复

普通 `supervisor` 负责 capture、stalled 判断、调度文档刷新和可选查询；`health supervisor` 负责另一件事：模仿 `Qwen-style codex2-supervisor.sh` 的末尾错误检测逻辑，发现 Codex tmux pane 停在可恢复错误上时自动发继续指令。

默认检测的典型错误包括：

- `stream disconnected before completion`
- `timeout waiting for child process to exit`
- `connection closed before message completed`
- `error sending request`
- `network error`
- `connection reset`
- `ECONNRESET` / `ETIMEDOUT`
- `502 Bad Gateway` / `503 Service Unavailable`

此外，health supervisor 会把下面这种主进程不可原线程恢复的错误识别为“上下文耗尽”：

```text
Codex ran out of room in the model's context window. Start a new thread or clear earlier history before retrying.
```

这类错误不能靠给老 pane 继续发送 prompt 解决。正确策略是启动一个新的主进程，并让它从 durable state 接管。

启动：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor \
  --interval 30 \
  --stable-seconds 20 \
  --cooldown 120
```

默认行为：

- 读取 `workers.json`，监控已注册主进程和所有未停止的 worker。
- 只对 `mode=interactive` 的 Codex worker 自动粘贴恢复 prompt。
- 对非 interactive worker 不自动恢复，避免把 prompt 当 shell 命令执行。
- 只有错误稳定存在达到 `--stable-seconds`，且距离上次恢复超过 `--cooldown`，才会发送恢复指令。
- 写入 `logs/health-supervisor.log`。
- 写入 `status/health_supervisor.json` 和 `status/health_supervisor_state.json`。
- 触发恢复时向 `schedule_events.jsonl` 追加 `health-recovery` 事件，并在 worker progress 文件里追加恢复记录。

把主进程也纳入监控并允许上下文耗尽后自动接管：

```bash
# 在主 Codex 所在 tmux pane 里获取 target
tmux display-message -p '#S:#W.#{pane_index}'

python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  register-coordinator --target <SESSION:WINDOW.PANE> --cwd "$PWD"

python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor \
  --restart-main-on-context-full \
  --restart-main-when-missing
```

当注册主进程出现上下文耗尽，或注册 target 直接消失且启用了 `--restart-main-when-missing` 时，health supervisor 会写入恢复调度事件，调用 `recover-coordinator`，刷新 `COORDINATOR_RECOVERY.md`，关闭旧主进程 pane，新开 `cw-main-recovered-...:codex` 之类的独立 tmux session，并启动新的 Codex 主进程继续调度。

如果不希望自动关闭旧主进程：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor \
  --restart-main-on-context-full \
  --restart-main-when-missing \
  --keep-old-main
```

手动接管：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  recover-coordinator --reason manual-restart --kill-old
```

如果只想观察某个 pane，而绝不自动发送恢复 prompt：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor \
  --observe-target shell-log=<SESSION:WINDOW.PANE>
```

建议先 dry-run 验证：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  start-health-supervisor --dry-run --force
```

停止：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  stop-health-supervisor
```

不要把 health supervisor 当成万能恢复器。它只处理网络断连、子进程退出等待超时、网关错误等“Codex TUI 卡在可恢复错误上”的情况。配额不足、认证错误、测试失败、代码冲突、指标退化、训练脚本真实崩溃，都应该交给主进程诊断，而不是自动续跑掩盖问题。

主进程上下文耗尽的自动接管也不是“凭空恢复记忆”。它依赖此前持续维护的 durable artifacts：`COORDINATOR_SCHEDULE.md`、`COORDINATOR_RECOVERY.md`、worker progress/report、jobs registry、branch-manager 汇总、peer messages 和项目自己的跟进文档。如果主进程从未记录任务目标、worker 启动原因、资源分配和阶段性判断，新主进程只能恢复到这些文件实际保存的信息。

### 9. 后台 Job 注册

worker 如果启动训练或评估后台进程，应注册 PID：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  job-add long-eval \
  --pid 12345 \
  --name train-main \
  --kind training \
  --log /path/to/train.log \
  --resource gpu:0 \
  --command "nohup python train.py > train.log 2>&1 &"
```

查看 jobs：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  jobs
```

停止 job：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  job-stop long-eval --name train-main --signal TERM
```

### 10. 状态查看

查看 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  list
```

常见状态：

- `running`: tmux target 存在，worker 活跃。
- `completed`: worker 退出码为 0。
- `failed`: worker 非 0 退出。
- `stopped`: coordinator 主动停止。
- `stalled`: supervisor 认为长时间没有变化。
- `not-present`: registry 中存在，但 tmux target 不存在。

查看进展：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  progress long-eval
```

查看输出：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  capture long-eval --lines 80
```

只有当 80-120 行不足以诊断具体问题时，才扩大 `--lines` 或读取 `captures/<worker>/`、`logs/`、worker report 指向的完整 artifact。

查看咨询上下文：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  consult-context --print
```

### 11. 恢复 worker

如果 worker 中断、tmux 窗口丢失、主进程重启，可以从 durable artifacts 恢复：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  resume long-eval --mode interactive
```

恢复时 worker 会读取：

- workplan
- progress
- report
- inbox
- jobs registry

然后继续未完成任务。

### 12. 收集与主进程收口

汇总所有 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  collect --lines 30
```

输出位于：

```text
.codex/tmux-workers/reports/COORDINATOR_SUMMARY_<timestamp>.md
```

汇总包含：

- worker 状态
- resources
- jobs
- progress tail
- report tail
- git status 和 diff stat

主进程应基于这个 summary 做最终判断。

`collect` 会刷新 `COORDINATOR_SCHEDULE.md`，但它不替代 `schedule-note`。`collect` 是证据汇总；`schedule-note` 是主进程的调度判断和下一步决策。

### 13. 推荐长程自主流程

```text
1. 主进程读取项目状态、目标、资源。
2. init 创建 state dir，并记录 tmux session 命名空间。
3. start-consult 启动只读用户咨询窗口。
4. 按任务拆分 worker：
   - 只读审计 -> exec worker
   - 长训练/长评估 -> interactive worker + supervisor
   - 并行代码修改 -> --git-worktree worker
   - 重大实验分支 -> branch-manager worker
5. branch-manager 在授权 scope 内启动带 --parent-worker 的一线 autonomous-experiment worker。
6. 一线 worker 之间只用 peer-send 传递短证据/依赖消息。
7. 每个 worker 声明 owned-path 和 resource。
8. supervisor 持续 capture、query、stalled 检测。
9. worker 启动后台任务时 job-add 注册 PID。
10. 主进程定期 list/progress --lines 40/jobs/collect --lines 30，优先看 branch-manager 汇总，必要时再短 capture。
11. 主进程用 schedule-note 记录调度判断和下一步 checkpoint，并用 consult-sync 更新咨询窗口。
12. worker 完成后，主进程 review report、diff、logs、metrics。
13. 主进程运行必要测试和最终评估。
14. 主进程决定合并、重跑、恢复或停止，并把决策写入调度文档。
```

### 14. 设计边界

已经支持：

- tmux 多窗口 Codex worker
- exec 和 interactive 两种 worker
- branch-manager 分支管理 worker
- peer-send worker 横向消息
- paste-buffer 通信
- inbox 指令队列
- progress/report/workplan/status 持久化
- supervisor capture/query/continue
- stalled 检测
- read-only 用户咨询 worker
- CONSULT_CONTEXT.md 咨询上下文快照
- background job registry
- optional git worktree isolation
- resume 和 collect

仍需主进程人工或半自动判断：

- git worktree 的最终 merge
- 实验指标是否通过 gate
- worker 输出是否可信
- 代码 diff 是否应接受
- 多 worker 对同一逻辑区域修改时的冲突解决

这套框架的原则是：worker 可以并行执行，主进程必须集中审查和最终负责。

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

Main tmux Codex worker manager. It provides commands to initialize state, register/recover the main coordinator, launch workers, send or interrupt prompts, start consultation windows, start normal and health supervisors, track background jobs, resume workers, collect reports, maintain unified coordinator constraints, compact coordinator memory, and maintain the coordinator context pack, memory, schedule, and recovery handoff documents.

Typical command:

```bash
MANAGER="${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py"

python "$MANAGER" --state-dir .codex/tmux-workers --session cw init --cwd "$PWD"
python "$MANAGER" --state-dir .codex/tmux-workers --session cw register-coordinator --target <SESSION:WINDOW.PANE> --cwd "$PWD"
python "$MANAGER" --state-dir .codex/tmux-workers constraints --tensorboard-port-range 16006-16099
python "$MANAGER" --state-dir .codex/tmux-workers compact-memory --print --context-pack
python "$MANAGER" --state-dir .codex/tmux-workers --session cw launch worker-a --cwd "$PWD" --task "Do one bounded branch task and report back."
```

Branch-manager and peer message example:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session cw launch branch-mgr \
  --cwd "$PWD" \
  --worker-kind branch-manager \
  --manager-scope "Coordinate this major branch and summarize child results." \
  --task "Plan child experiments, launch child workers, and report branch-level results."

python "$MANAGER" --state-dir .codex/tmux-workers --session cw launch child-a \
  --cwd "$PWD" \
  --parent-worker branch-mgr \
  --worker-kind autonomous-experiment \
  --task "Run one bounded child experiment and report evidence paths."

python "$MANAGER" --state-dir .codex/tmux-workers peer-send child-a child-b \
  --message "Child A produced artifact path results/child-a/metrics.json for Child B to inspect."
```

### `codex_tmux_health_supervisor.py`

Low-level health supervisor used by the manager's `start-health-supervisor` command. It monitors tmux Codex panes for recoverable transport/subprocess failures and sends a bounded continuation prompt when a pane is stuck. When the main coordinator is registered and `--restart-main-on-context-full` is enabled, it treats context-window exhaustion as fatal to the old thread and launches a recovered coordinator from `COORDINATOR_RECOVERY.md`.

Typical direct dry-run:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_health_supervisor.py" \
  --state-dir .codex/tmux-workers \
  --session cw \
  --once --dry-run
```

Prefer invoking it through the manager for real long-running use:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session cw start-health-supervisor --interval 30
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
  --session cw \
  init --cwd "$PWD" --mission "Coordinate a long-running autonomous project."

python "$MANAGER" \
  --state-dir .codex/tmux-workers \
  --session cw \
  launch branch-a \
  --cwd "$PWD" \
  --worker-kind autonomous-experiment \
  --write-scope "Run one bounded experiment branch and report results." \
  --task "Inspect the project, run a small validation experiment, update progress, and write a report."
```

For long-lived sessions, also start the two monitor layers:

```bash
python "$MANAGER" --state-dir .codex/tmux-workers --session cw start-supervisor --interval 300
python "$MANAGER" --state-dir .codex/tmux-workers --session cw start-health-supervisor --interval 30 --restart-main-on-context-full --restart-main-when-missing
```

Attach to tmux sessions:

```bash
tmux ls | rg '^cw(-|:)'
tmux attach -t cw-branch-a
```

## Safety Model

- The coordinator owns task decomposition, final review, integration, and user-facing conclusions.
- Workers must receive bounded objectives, write scopes, resource ownership, and expected reports.
- Branch managers may coordinate child workers inside their assigned scope, but final merge, promotion, cross-branch resource decisions, and user-facing conclusions remain coordinator-owned unless explicitly delegated.
- Worker-to-worker communication must go through `peer-send`; peer messages are evidence/dependency notes, not authority to change scope or resources.
- The framework records worker state under `.codex/tmux-workers/` so users can audit launches, inbox messages, progress, reports, captures, jobs, and scheduling decisions.
- The coordinator should maintain `COORDINATOR_CONSTRAINTS.md` before launching workers. All launched/resumed workers, branch managers, consultation workers, and recovered coordinators are instructed to read it before task-specific prompts.
- The coordinator should use `COORDINATOR_CONTEXT_PACK.md` and `COORDINATOR_MEMORY.md` as short working memory, and should run `compact-memory --note ... --decision ... --next-action ...` after meaningful decisions.
- Register tmux-hosted main coordinators with `register-coordinator` when long autonomous recovery matters. A recovered coordinator must start from `COORDINATOR_RECOVERY.md` and `COORDINATOR_SCHEDULE.md`, not from stale memory.
- Default coordinator checks should start from `compact-memory --print --context-pack`, `list`, `jobs`, and `progress --lines 20`; schedule, collect, larger captures, or raw artifacts are for concrete diagnosis or final review.
- The health supervisor targets transient Codex pane stalls and, when explicitly enabled, registered-coordinator context exhaustion. It is not a replacement for debugging quota/auth failures, failed tests, merge conflicts, bad metrics, or missing durable project documentation.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
