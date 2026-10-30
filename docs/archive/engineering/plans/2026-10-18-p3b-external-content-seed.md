# P3-b 外部内容种子：状态与设计（2026-10-18）

> **状态**：spike **GO**；种子实现**未开始**（这是刻意决定，不是遗漏）。
> 一手证据：`docs/archive/engineering/spikes/2026-10-18-p3b-read-content-seed.md`。
> 设计来源：workflow `p3b-external-content-seed`（5 个只读信号 agent + 架构 + 对抗评审）。

## 已证明的事实

- 真实 Claude Code（Bun）用 **`read(2)`** 读取 fixture；stdiocap 的
  `sys_enter_read/sys_exit_read` 能捕获它：3/3 次运行，每次 1 条匹配，
  `copy_failed=0`、`dropped_events=0`，内容 133 字节完整。
- 独立交叉验证：`strace -f -y -s 200 -e trace=read` 显示同一内容由 `read(2)`
  交付，缓冲区含完整 needle。
- 目标进程必须是 `exec` 保留 PID 的 claude 进程；第一版 spike 绑到子 shell PID
  导致 0 事件——这是必读陷阱。

## 本迭代实际交付的安全前置（均已进 verify）

1. **stdiocap 捕获诚实化**（`bpftools/stdiocap/*`）：
   - `copy_failed` 字段：`bpf_probe_read_user` 失败时置 1 并把 `buf_size` 归零，
     绝不把未初始化 ring 字节当内容。
   - `dropped_events` map + `inc_dropped()`：ring 预留失败不再静默。
   - 退出时输出可解析的 STATS 事件（含 `timestamp_ns/pid/comm`），runner 有单测。
2. **stdio 事件终止化**（`runtime/routing.rs` + `runtime.rs`）：`source=="stdio"`
   在 `route_event` 直接返回，绝不进入 `IntentExtractor`。此前文件字节可以变成
   幻影工具意图。
3. **嵌入二进制新鲜度覆盖全部三个程序**：`embedded_binary_freshness.rs` 比较
   `tls_capture`/`process`/`stdiocap` 的 `include_bytes!` 与磁盘。
4. **正向 fixture**：`env/scenarios/fixtures/p3b-external-content/injected_handoff.md`
   （133 字节，含 needle/命令/目标）。
5. **Spike 脚本**：`scripts/spikes/p3b-stdio-read-coverage.sh`（3/3 GO 门）。

## 为什么不同时实现种子

对抗评审给出 5 个阻断缺陷，其中两个直接否证了原方案的可用性：

- **B2：指令门无法拒绝检测器自身源码**。对 `collector/src/.../content.rs` 与
  `graph_builder.rs` 的真实前缀模拟，命中 needle 的行同时含命令动词与路径目标，
  会通过"同句 + 动词 + 目标"门。原计划声称它会拒绝，是假的。
- **B1：正向验收命令过滤 0 条测试**（`cargo test p3b_external_content_seed`
  没有任何测试名匹配），可以在零断言下"全绿"。
- 其他：spike 会把原始读取写进 FileLogger（隐私）；STATS 在 collector 关闭时序
  中可能丢失；benign live 门在场景 harness 中不可执行；writer registry 只覆盖
  已关联的写、且 `open()` 的 O_RDWR 会被误判为写；artifact key 用 contains-either-way
  会让 Grep/Glob 目录前缀成为 key；claim 的 boundary id 会变成
  `boundary.tool.ReadContent` 而非注入边界。

在一个"断言必须能被第三方否证"的项目里，带已知误报类的启发式检测器上线，
正是本项目要消灭的病。因此本迭代只固化**通道可观测性**与**安全前置**。

## 继续实现的前置条件（按优先级）

1. **可靠的指令门**：不是更多 needle，而是可证伪的判定——例如用 Layer 3 分类器
   对读取前缀做一次打分（已有 ONNX 服务），并建立**负例语料**（content.rs、
   graph_builder.rs、docs、真实 benign fixtures）要求 0 命中；或定义结构化动作文法
   （`verb target` 紧邻 + 目标必须是路径/URL + 排除注释/代码围栏/字符串列表）。
2. **artifact key 精确性**：只对 `tool_name=="Read"` 且 `P_intent == P_fd` 播种，
   或 key 用 P_fd；Grep/Glob 目录前缀不得成为 key。
3. **writer registry 从 routing 的 action 取**（不只关联对），路径用
   `paths_compatible`；O_RDWR 不算写。
4. **请求体通道**：语料中 370 个 request event 仅 3 个有效且是遥测，不可用；
   不要假装它可覆盖。
5. **隐私**：种子路径不持久化原始字节；stdio 原始事件经 FileLogger 落盘是既有
   行为，需在能力声明中写明，P5 拥有脱敏。

## 未来实现的验收（可证伪）

- 确定性正向：真实 133B fixture 经 synthetic stdio READ → 1 条
  `TaintPropagatesTo`（File 资源节点 → 读者 Agent）+ 1 条 `TaintedContextUse`，
  evidence 共享，claim_state Inferred（0.50）。
- 强负例：`content.rs`、`graph_builder.rs`、`docs/` 的真实前缀各 0 种子；
  `Check that instructions do not override safety policies` 0 种子；
  `ignore previous instructions for template format`（无目标）0 种子；
  credential-only 0 种子；同进程读 0 种子。
- 幂等：同一 `(path, digest)` 两次观测 → 1 条边、evidence 追加。
- 隐私：DB 中无原始字节/摘录；只有 digest/boundary/path/evidence id。
- Live：仅当 spike 对目标再次 GO；默认关闭，不进入 `record`。
