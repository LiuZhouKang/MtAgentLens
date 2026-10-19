---
status: accepted
date: 2026-10-18
---

# 0007 — 文件变更生产者并入 `process`，删除未接线的 `fileaccess`

## Context

行动层需要「实际发生的文件变更」来给 agent 归因，但证据显示它从未见过删除/改名/截断：

- 301 个真实归档库的 `actions` 聚合：FileRead 93,530 / ProcessExec 18,555 /
  FileOverwrite 3,386 / FileWrite 3,288，**FileDelete、FileRename、
  FileTruncate 为 0**。
- `bpftools/process/process.bpf.c` 只挂 `sys_enter_open(at)` 并发
  `FILE_OPEN`；FileWrite/FileOverwrite 是从 open flags 推导的。
- `bpftools/fileaccess` 是独立程序，**没有被 collector 启动**；即使启动也只覆盖
  open/openat，功能与 `process` 重复。
- Rust 侧 `routing.rs` 早已把 `FILE_DELETE`/`FILE_TRUNCATE` 路由到
  `unlink`/`write`，`ActionClassifier` 也把 `unlink` 分类为 `FileDelete` ——
  有消费者、无生产者，是完整的幽灵事件链。
- 内核下限已是 5.8（`docs/capabilities.json`），fentry 可用，但这不改变
  「先补现有 syscall 生产者」的性价比。

## Decision

1. **删除 `bpftools/fileaccess`**（及其两份过期文档）。它未被接线、与 `process`
   重复，保留只会让「有没有文件观测」这个问题有两个互相矛盾的答案。
2. **在 `process` 中新增三个 syscall tracepoint 生产者**：
   - `tp/syscalls/sys_enter_unlinkat` → `FILE_DELETE`
   - `tp/syscalls/sys_enter_truncate` → `FILE_TRUNCATE`
   - `tp/syscalls/sys_enter_rename(`at2`)` → `FILE_RENAME`（源/目标双路径）
   事件复用 `EVENT_TYPE_FILE_OPERATION`，新增 `enum file_op_kind` 与
   `file_op.op` 字段；JSON 行由 `process/process_json.h` 统一打印，`event`
   字段取 `FILE_OPEN`/`FILE_DELETE`/`FILE_TRUNCATE`。
3. **消费者契约钉死**：`routing.rs` 新增单测，断言上述事件映射到
   `unlink`/`write`（即 ActionClassifier 能分类的 syscall 名）。
4. **`FILE_RENAME` 已在同一次改动中补上**：`struct event.file_op` 增加
   `filepath2`，`sys_enter_rename`/`sys_enter_renameat2` 发出 `FILE_OP_RENAME`；
   `filepath`=源、`filepath2`=目标。
   **2026-10-18 更新**：动作层已新增 `ActionCandidateType::FileRename`，两条路径
   都进入 `normalized_args`（源 + 目标）；旧库里的历史行仍读作
   `FileOverwrite`，不做重写（迁移说明见下）。
5. **仍不在本 ADR 解决**：
   - `NETWORK_CONNECT`/`NETWORK_SEND` 需要 `sys_enter_connect/sendto` 与
     host/port 解析，独立决策（已由 ADR 0010 解决）。
   - `FILE_WRITE` 继续由 open flags 推导，未新增 `sys_enter_write`（fd→path
     需要额外跟踪，收益/复杂度比不划算）。

## 更新（2026-10-18）：rename 不再是 FileOverwrite 近似

- `ActionCandidateType::FileRename` 是独立类型；`rename`/`renameat`/`renameat2`
  归类为它，`FileRename` 属于 HighSignal。
- `extract_action_args` 对 rename 保留源与目标两条 `NormalizedArg::Path`；
  持久化仍用 `format!("{:?}")`，reader 已补 `"FileRename"` 映射并用单测钉死
  全部 9 个变体的往返。
- 图构建把源路径作为资源节点（与 delete 一致）；目标路径在动作参数与原始事件
  中可得。把污染状态随 rename 迁移到目标路径属于 false-taint 工作（P2），
  不在本次范围。
- **数据迁移**：没有 DDL 变化，历史行不重写。它们是近似记录，不是错误；重写历史会让
  “当时系统怎么判断的”无法复原。近似值取决于当时的代码：
  已提交的 HEAD 把 `FILE_RENAME` 路由到 `write`（读回 `FileWrite`），
  P4 工作树把 `rename` 分类为 `FileOverwrite`。两种都可能出现在旧库里。

## 一个环境限制（不是产品限制）

本开发 harness 对其自身 spawn 的进程做了文件 syscall 拦截：`touch`/`rm`/
`git` 等不被主机 tracepoint 观测，`bpftrace` 交叉验证同样为 0；只有宿主机长驻
进程（git watcher、systemd-oomd）可见。因此本环境的 E2E 只能证明
**加载/attach/verifier 通过**与 **JSON/路由契约**，不能证明运行时事件率。
`scripts/spikes/file-mutation-hooks.sh` 记录了在非沙箱环境复现的步骤；产品的
正常运行环境（用户终端）不受此限制。

## Confirmation

- `make -C bpftools test` 全过，新增
  `test_process_file_ops`：`op` → `FILE_DELETE`/`FILE_TRUNCATE`/`FILE_RENAME`/
  `FILE_OPEN`、`filepath2`、agent 可控字节转义、未知 op 降级为 `FILE_OPEN`。
- `cargo test --lib routing`：`file_mutation_events_map_to_classifiable_syscalls`
  覆盖 unlink/write/rename（双路径）。
- `sudo .output/bin/process` 通过 verifier 加载并 attach（首次实现因
  「dereference of modified ctx ptr」被拒，已改为在 handler 内用常量索引取参）。
- `./scripts/verify.sh` 13 门全绿；`docs/capabilities.json` 的幽灵事件条目从
  7 类降为 5 类。
- 一处重复头文件被顺手消除：`include/process.h` 与 `process/process.h`
  是同一 `struct event` 的两份副本，改一份另一份静默不生效；现在只剩
  `process/process.h`（自包含 `<stdbool.h>`）。
