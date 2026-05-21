# Codex Tmux 自主并行框架说明

## 1. 目标

这套框架用于让一个主 Codex 在长时间自主模式下稳定运行，并通过 tmux 统一调度多个独立的子 Codex 进程协作完成任务。

核心目标是：

- 主进程长期在线，负责拆解任务、资源规划、最终判断和结果合并。
- 子 Codex 在独立 tmux window 中运行，负责支线执行、实验巡检、报告草拟、代码修改或数据分析。
- 所有 worker 都有可恢复的本地状态：任务计划、进展、报告、日志、inbox、status、后台 job registry。
- 主进程可以定期监督、发送指令、暂停/恢复 worker，并在最后收集所有证据。

## 2. 架构

```text
主 Codex / Coordinator
  |
  |-- codex_tmux_manager.py
  |     |
  |     |-- tmux session: codex-workers
  |     |     |-- window: cw-worker-a      -> Codex worker A
  |     |     |-- window: cw-worker-b      -> Codex worker B
  |     |     |-- window: cw-consult       -> 只读用户咨询 Codex
  |     |     |-- window: cw-supervisor    -> supervisor loop
  |     |     |-- window: cw-health-supervisor -> 错误卡死健康守护
  |     |
  |     |-- .codex/tmux-workers/
  |           |-- workers.json             -> worker registry
  |           |-- COORDINATOR_SCHEDULE.md  -> 主进程调度总览文档
  |           |-- schedule_events.jsonl     -> 调度事件日志
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

主进程只把适合并行的支线拆给 worker。任何最终合并、实验结论、代码接受、指标判断，都必须由主进程复核。

## 2.1 默认模型策略

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

## 2.2 专用用户咨询窗口

长程自主任务中，用户经常只想询问“现在有哪些 worker、谁在跑什么、证据在哪里、下一步是什么”，不一定要打断主进程。框架因此提供一个常驻的只读咨询 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  start-consult --cwd "$PWD"
```

默认位置：

```text
tmux: codex-workers:cw-consult
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

用户可以 `tmux attach -t codex-workers`，切到 `cw-consult` 窗口直接提问。这样主进程可以继续保持长程自主推进，不必因为状态咨询被频繁打断。

## 3. 基本初始化

在项目根目录执行：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  init --cwd "$PWD" \
  --mission "本轮长程自主任务目标"
```

这会创建 tmux session 和项目本地状态目录。

初始化后会生成：

```text
.codex/tmux-workers/COORDINATOR_SCHEDULE.md
```

这是主进程维护的调度总览文档，用户可以直接审查它来理解当前有哪些 worker、为什么启动、各自任务是什么、资源怎么分配、结果在哪里、下一步是什么。

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
  --session codex-workers \
  start-consult --cwd "$PWD"
```

## 3.1 主进程调度文档

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
- 每个 worker 的状态、任务、模型、reasoning effort、资源、owned paths、tmux 位置
- workplan/progress/report/inbox/jobs/status 文件路径
- 后台 job 当前 PID 状态
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

## 4. 启动 worker

### 4.1 一次性 exec worker

适合日志审计、结果汇总、只读分析、短脚本生成。

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  launch result-audit \
  --cwd "$PWD" \
  --mode exec \
  --write-scope "read-only: results/, logs/, docs/" \
  --owned-path "docs/result-audit.md" \
  --resource "cpu:analysis" \
  --task "Inspect current experiment outputs and report complete metrics, missing files, and next evaluation."
```

### 4.2 interactive worker

适合长时间任务、需要后续指令的任务、需要 supervisor 查询的任务，以及用户希望直接在 tmux 里看到 Codex worker 操作过程的任务。interactive worker 使用 `--no-alt-screen`，所以 Codex 的计划、命令、诊断和输出会留在 tmux window 的 scrollback 里。

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
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

### 4.3 visible autonomous experiment worker

如果希望 worker 不只是做日志调查，而是独立推进实验支线，并且用户可以在 tmux 中看到 Codex worker 的具体运作过程，使用：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
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
- 启动后在 worker window 中粘贴任务 prompt。
- 自动启动 `cw-supervisor`，除非显式加 `--no-start-supervisor`。
- 要求 worker 在可见 tmux pane 中说明关键计划、短命令检查、失败诊断、实验启动和阶段性判断。
- 长训练/评估仍应作为后台 job 运行并通过 `job-add` 注册 PID/log/resource；Codex worker pane 负责展示“为什么跑、如何诊断、下一步是什么”，而不是只展示训练日志。

用户查看方式：

```bash
tmux attach -t codex-workers
```

然后切换到 `cw-exp-a` 这样的 worker window。

## 5. Git Worktree 隔离

多个 worker 同时改代码时，推荐使用独立 git worktree：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
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

## 6. 通信机制

### 6.1 短指令

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  send long-eval "Please summarize current progress in the progress file."
```

发送使用 `tmux set-buffer` + `tmux paste-buffer` + Enter，适合多行 prompt。

### 6.2 长指令或重要指令

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

### 6.3 打断 busy worker

如果 worker 正在前台执行命令，需要立刻切换到新指令，使用 `interrupt-send`：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  interrupt-send long-eval "Pause current work, save progress, and report state."
```

注意按键顺序：`interrupt-send` 会先通过 tmux paste-buffer 粘贴并回车提交新消息，再发送 `Escape`，让 Codex TUI 从当前运行切到刚提交的新指令。不要手工改成“先 Escape 再发消息”，否则 busy worker 容易只是取消当前输入或把新消息排队到下一次工具调用之后。

## 7. Supervisor

启动 supervisor：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  start-supervisor \
  --interval 300
```

不要在主进程里直接运行无界 `supervise`。现在 `supervise` 默认只允许 `--once` 前台检查；无界循环必须通过 `start-supervisor` 进入 tmux 的 `cw-supervisor` 窗口，避免主 Codex 触发 unified exec warning。

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

## 8. Health Supervisor：错误卡死自动恢复

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

启动：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  start-health-supervisor \
  --interval 30 \
  --stable-seconds 20 \
  --cooldown 120
```

默认行为：

- 读取 `workers.json`，监控所有未停止的 worker。
- 只对 `mode=interactive` 的 Codex worker 自动粘贴恢复 prompt。
- 对非 interactive worker 不自动恢复，避免把 prompt 当 shell 命令执行。
- 只有错误稳定存在达到 `--stable-seconds`，且距离上次恢复超过 `--cooldown`，才会发送恢复指令。
- 写入 `logs/health-supervisor.log`。
- 写入 `status/health_supervisor.json` 和 `status/health_supervisor_state.json`。
- 触发恢复时向 `schedule_events.jsonl` 追加 `health-recovery` 事件，并在 worker progress 文件里追加恢复记录。

把主进程也纳入监控：

```bash
# 在主 Codex 所在 tmux pane 里获取 target
tmux display-message -p '#S:#W.#{pane_index}'

python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  start-health-supervisor \
  --watch-target main=<SESSION:WINDOW.PANE>
```

如果只想观察某个 pane，而绝不自动发送恢复 prompt：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  start-health-supervisor \
  --observe-target shell-log=<SESSION:WINDOW.PANE>
```

建议先 dry-run 验证：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  start-health-supervisor --dry-run --force
```

停止：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  --session codex-workers \
  stop-health-supervisor
```

不要把 health supervisor 当成万能恢复器。它只处理网络断连、子进程退出等待超时、网关错误等“Codex TUI 卡在可恢复错误上”的情况。配额不足、认证错误、测试失败、代码冲突、指标退化、训练脚本真实崩溃，都应该交给主进程诊断，而不是自动续跑掩盖问题。

## 9. 后台 Job 注册

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

## 10. 状态查看

查看 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  list
```

常见状态：

- `running`: tmux window 存在，worker 活跃。
- `completed`: worker 退出码为 0。
- `failed`: worker 非 0 退出。
- `stopped`: coordinator 主动停止。
- `stalled`: supervisor 认为长时间没有变化。
- `not-present`: registry 中存在，但 tmux window 不存在。

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
  capture long-eval --lines 160
```

查看咨询上下文：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  consult-context --print
```

## 11. 恢复 worker

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

## 12. 收集与主进程收口

汇总所有 worker：

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/general/tmux-codex-parallel-workers/scripts/codex_tmux_manager.py" \
  --state-dir .codex/tmux-workers \
  collect
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

## 13. 推荐长程自主流程

```text
1. 主进程读取项目状态、目标、资源。
2. init 创建 tmux session 和 state dir。
3. start-consult 启动只读用户咨询窗口。
4. 按任务拆分 worker：
   - 只读审计 -> exec worker
   - 长训练/长评估 -> interactive worker + supervisor
   - 并行代码修改 -> --git-worktree worker
5. 每个 worker 声明 owned-path 和 resource。
6. supervisor 持续 capture、query、stalled 检测。
7. worker 启动后台任务时 job-add 注册 PID。
8. 主进程定期 list/progress/jobs/collect。
9. 主进程用 schedule-note 记录调度判断和下一步 checkpoint，并用 consult-sync 更新咨询窗口。
10. worker 完成后，主进程 review report、diff、logs、metrics。
11. 主进程运行必要测试和最终评估。
12. 主进程决定合并、重跑、恢复或停止，并把决策写入调度文档。
```

## 14. 设计边界

已经支持：

- tmux 多窗口 Codex worker
- exec 和 interactive 两种 worker
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
