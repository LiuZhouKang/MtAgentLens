# 实验隔离：让测量只属于被测 Agent

> 状态：已实现并验收（2026-10-19）。适用对象：Claude Code、OpenClaw 等
> 通过 `claude`/自定义 ELF 启动的 Agent。

## 1. 为什么需要两层隔离

之前的 FPR/开销测量有两个独立污染源：

1. **Agent 配置污染**：被测 Agent 继承操作者的 `~/.claude`——插件、MCP、
   hooks、skills、memory、项目 `CLAUDE.md` 都会进入场景，测到的不是场景本身。
2. **采集面污染**：collector 用 `-c claude` 过滤，运行测量的交互会话与被测
   Agent 同名，于是外层会话的 TLS/进程事件也进入同一个 DB。2026-10-19 的
   开销重测因此得到 5,301 事件 / 1,836% CPU（基线为 883 事件 / 6.24%）。

历史实现 `env/scripts/run_lab.py --clean-profile`（2,002 行，commit
`1e933a9`/`ba17e45`）曾解决第 1 点；迁移到 `mtagentlens_benchmark` 时该文件
被删除，只剩一个断链测试。第 2 点从未实现。

## 2. 现在的机制

### 2.1 配置隔离（`clean_profile.py`）

每次 run 生成：

```
<run_dir>/clean-profile/settings.json   {"enabledPlugins":{},"hooks":{},"mcpServers":{},"autoMemoryEnabled":false}
<run_dir>/clean-profile/mcp.json        {"mcpServers":{}}
<run_dir>/clean-profile/manifest.json   本次隔离了什么
```

并给 Claude 追加：

```
--bare --disable-slash-commands --strict-mcp-config
--mcp-config <clean>/mcp.json --settings <clean>/settings.json
--setting-sources local
```

环境变量收窄到白名单（`ANTHROPIC_*`、`MODEL`、`PATH`、`HOME` 等），并强制
`CLAUDE_CODE_SIMPLE=1` / `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`。
manifest 写进 `summary.json` 的 `clean_profile` 字段，可事后审计。

### 2.2 采集面隔离（inode 副本 + 唯一 comm）

`clean_profile.isolated_agent()` 把真实 Claude ELF **复制**到临时目录，
副本名为 `mtl-<pid>`（≤15 字节，避免 `TASK_COMM_LEN` 截断）：

- **TLS**：`tls_capture` 的 uprobe 按 inode 挂载。副本与交互会话的原始
  ELF 是不同 inode，所以只有副本的 `SSL_read/SSL_write` 被捕获；外层会话
  使用原 inode，永远不会进入测量。
- **进程**：eBPF FILTER 模式按 `comm` 匹配，副本的 `comm` 是 `mtl-<pid>`；
  collector 的 `-c` 与 `MTAGENTLENS_AGENT_COMMS` 都使用同一个名字。
- 不能用 symlink：symlink 与目标共享 inode，uprobe 仍会捕获外层会话。

runner 侧（`scripts/wire/fpr_runner.py`）为整批场景创建一个副本，
`--agent-binary` / `--agent-comm` 传给 benchmark，`MTAGENTLENS_CLEAN_PROFILE=1`
开启配置隔离。

### 2.3 健康检查的适用性修正

- **canary**：curl 的 `comm=curl`，在 comm 过滤下必然不被捕获。现在标记
  `skipped` 并给出原因，而不是报 “TLS capture inactive”。
- **uprobe 启动检查**：libbpf 用 `perf_event_open` 挂 uprobe，不会在
  `tracefs/uprobe_events` 留条目；空文件现在记 `note`，捕获健康以运行时
  `tls_sessions` 为准。

## 3. 验收证据（2026-10-19，本机）

被测场景：`benign-api-doc`；同时用**原始二进制**发起一次并行的外层请求
（PID 957705）。

```
process_events comms : ["git", "mtl-957706"]     # 无外层 "claude"
tls_sessions pids    : [958002]                  # = 内层 agent PID
外层 PID             : 957705  (不在 tls_sessions)
clean_profile        : enabled=true, flags=[--bare,--disable-slash-commands,--strict-mcp-config]
comm_filter          : mtl-957706
canary               : skipped（comm 过滤排除 curl）
```

`process_events` 为 0 的旧问题也定位并修复：最初用 16 字符的 comm，内核截断为
15 字符导致精确匹配失败；现在名字固定 ≤15 字节。

### 3.1 20 场景隔离 FPR（2026-10-19）

`docs/evaluation/fpr-baseline.json`：20/20 observed、
Warning+ **0/20**（Wilson 0–0.161）、Unsafe 0/20、0 claims；每个 run 的
`process_comms` 只有副本 comm 与工具子进程，没有外层 `claude`。

## 4. 怎么跑

```bash
# 20 个良性场景，自动使用隔离副本 + clean profile
./scripts/wire/run-benign-fpr.sh /tmp/fpr-isolated

# 单场景调试（CLI 直接支持）
isolated=$(mktemp -d); cp --reflink=auto "$(readlink -f "$(command -v claude)")" "$isolated/mtl-$$"
MTAGENTLENS_CLEAN_PROFILE=1 mtagentlens-benchmark run \
  --scenario benign-api-doc --mode process --collector \
  --agent-binary "$isolated/mtl-$$" --agent-comm "mtl-$$"
```

每个结果记录 `isolation.agent_binary` / `isolation.agent_comm` /
`isolation.clean_profile` 与 `process_comms`，便于判断该 run 是否真的隔离。

## 5. 限制

- 副本是完整 ELF（Claude Code 约 273 MiB）；一次批量测量创建一个，用完删除。
  需要 `cp --reflink=auto` 时优先 reflink，否则退化为普通复制。
- 只解决“同一二进制的外层会话”污染；机器上其他 Agent/进程仍可能产生噪声
  （它们不在 `-c` 过滤内，但系统级指标如 CPU/RSS 仍受影响）。开销测量应在
  空载主机上进行。
- tmux 模式同样使用副本与 clean profile；但 tmux pane 内的子进程继承副本
  comm，收尾时 `kill-session` 必须先于删除副本（runner 已按此顺序）。
- OpenClaw 通过自身 sqlite/stdio 采集时，不使用本机制；其隔离依赖
  `MTAGENTLENS_AGENT_COMMS/_CGROUP/_EXE` 与 cgroup 边界。对**先于采集器启动**
  的长驻 Gateway，必须用 `-p <gateway_pid>`（探针已把 target PID 的直接子进程
  纳入跟踪），单靠 `-c MainThread` 只能看到采集器启动之后的新进程。
