# 威胁模型（STRIDE）

> 本文只描述**当前代码**能证明的边界；每条缓解都给出文件/函数坐标。
> 与 `SECURITY.md` 的「已知残余风险」配套阅读。

## 1. 资产

| 资产 | 位置 | 为什么敏感 |
|---|---|---|
| 解密后的 agent 明文 | eBPF ring buffer → `data_b64` → 运行库/日志/SSE | 含 prompt、工具参数、响应体、HTTP 头（可能含凭据） |
| 运行库 | `env/runs/<id>/collector/collector.responsibility.db` | 全量意图/动作/证据/告警/图快照 |
| 告警与责任链 | `responsibility_alerts` / `claims` / `tracebacks` | 对 agent 行为的归因结论 |
| 导出 span | `export-genai` 输出 | 离开信任边界的副本 |
| 认证材料 | `collector/src/auth/`（TOTP secret、备份码） | 可绕过 UI 认证 |
| 构建产物 | `collector/build/`、`bpftools/.output/` | 被篡改的二进制会以高权限运行 |

## 2. 信任边界

```
内核/eBPF ──(ringbuf)──▶ collector(root/CAP_BPF) ──▶ 磁盘(运行库/日志)
                                      │
                                      ├──▶ 回环 HTTP :7395 ──▶ 浏览器 UI
                                      └──▶ 导出 JSONL（可选）
被监测 agent（不可信内容）──▶ 解析器/检测器（同进程）
```

| 边界 | 进入方 | 不可信内容 |
|---|---|---|
| B1 内核→collector | 被监测进程 | 任意字节（TLS 明文、进程名、路径） |
| B2 collector→磁盘 | collector | 捕获内容原样落盘 |
| B3 collector→浏览器 | 本机网页/用户 | 读取运行库与告警 |
| B4 collector→外部 | 导出目标 | 内容是否越界 |
| B5 agent→检测器 | 被监测 agent | prompt injection、伪身份、路径伪造 |

## 3. 威胁与缓解（按 STRIDE）

### S — 身份伪造

| 威胁 | 缓解 | 坐标 |
|---|---|---|
| PID 复用把新进程算作旧 agent | 事件携带 `start_time_ns`/`parent_start_time_ns`；`ProcessId=(pid,start_time)` | `process/process.bpf.c`、`types/ids.rs` |
| agent 自称角色（claimed_role）被当真 | 身份解析只用三个观测信号（root_pid / tls_session / conversation_id）；`claimed_role` 仅展示 | `responsibility/identity/resolver.rs`、`export/genai.rs` 只导出 `agent.id` |
| 阶段 1 的临时身份覆盖权威结论 | `identity_process_beliefs` 追加式 + 视图取**最强**信念（Authoritative > Inferred > Provisional > Unknown） | `persistence/schema.rs`（v2 视图） |

### T — 数据篡改

| 威胁 | 缓解 | 坐标 |
|---|---|---|
| 运行库被同机其他用户读取/篡改 | 可写打开收紧为 `0600` | `persistence/schema.rs::restrict_file_permissions` |
| 旧 schema/外来库被误写 | 任何 PRAGMA 前只读分类；causal/unknown/未来版本零写入拒绝 | `persistence/schema.rs::classify_schema` / `open_responsibility_db` |
| 迁移中途失败留下半套 DDL | 每个迁移 `BEGIN IMMEDIATE` + 失败 ROLLBACK；重开可恢复 | `persistence/schema.rs::apply_migration` |

### R — 抵赖

| 威胁 | 缓解 | 坐标 |
|---|---|---|
| 无法证明告警来自哪次采集 | 每次采集一个运行库 + `runs` 行；告警绑定 `run_id`；证据引用 `evidence_refs` | `persistence/schema.rs`、`claims/claim_builder.rs` |
| 回放结果与原始不一致却被当作一致 | replay 报告按语义键做**多重集**比较；差异进入 `gap_sections` | `bin/replay.rs::Comparison` |

### I — 信息泄露（本产品的主威胁）

| 威胁 | 缓解 | 坐标 |
|---|---|---|
| 原始事件含 `Authorization` 并进入日志/SSE | `RawSecretRedactor` 在输出前重写头值，事件留 `redaction` 标记 | `framework/analyzers/raw_secret_redactor.rs` |
| MCP stdio 的 JSON-RPC 工具参数含 token/password 并进入日志/SSE | `StdioSecretRedactor` 在输出前对 `data` 应用形状化脱敏（复用 `export::genai::redact`），事件留 `redaction` 标记；捕获本身不改 | `framework/analyzers/stdio_secret_redactor.rs` |
| 导出把 prompt/标题/推理带出边界 | 默认只导出 id/枚举/计数/时间戳；`--include-content` 需显式且仍脱敏 | `export/genai.rs::ExportPolicy` |
| 恶意网页读取回环 API | 默认仅绑 `127.0.0.1`；移除所有 `Access-Control-Allow-Origin: *` | `main.rs::start_web_server_if_enabled`、`server/web.rs` |
| 原始字节被误认为已脱敏 | `redaction.captured_buf_size` 保留原长，`redacted_buf_size` 记录新长；捕获层不改字节 | `raw_secret_redactor.rs::redact_event` |
| 凭据出现在测试/文档 | 测试使用合成值；`docs/capabilities.json` 明示原始事件敏感性 | `raw_secret_redactor.rs` tests |

### D — 拒绝服务

| 威胁 | 缓解 | 坐标 |
|---|---|---|
| agent 高频 open/write 打满 ring buffer | eBPF 端噪声过滤 + `dropped_events` 计数；userspace 聚合窗口 | `process/process.bpf.c`、`process/process.c` |
| 超长/畸形 TLS 载荷拖垮解析 | 解码失败产生显式 `decode_status` 诊断，不静默吞掉；分片标记 `partial` | `framework/normalize/transport.rs`、`payload_normalizer.rs` |
| 回放超大库 OOM | 离线解码预算门（吞吐 + 峰值 RSS） | `collector/tests/overhead_gate.rs` |

### E — 权限提升

| 威胁 | 缓解 | 坐标 |
|---|---|---|
| 被监测 agent 通过伪造事件影响 collector | 事件只作为数据；不执行捕获内容；检测器纯函数化 | `runtime/routing.rs`、`analyzers/*` |
| eBPF 程序越界读写内核内存 | 仅用 verifier 允许的 helper；路径读取区分 user/kernel 指针；`make test` + 真实加载验证 | `process/process.bpf.c`、`tls_capture/*` |
| 构建产物被替换 | `scripts/verify.sh` 唯一门禁；eBPF/固件二进制来源可重建 | `scripts/verify.sh` |

## 4. 残余风险（不缓解 / 部分缓解）

1. 请求体与工具参数只做**形状化**脱敏：SSL body 不做通用脱敏，stdio `data` 由
   `StdioSecretRedactor` 按已知凭据形状重写；JSON 输入用 JSON 感知模式
   （对象成员、转义 JSON 字符串、敏感键下的数组/数值、字符串内的 header/label），
   只替换值、保持结构有效。没有可识别前缀或键名的随机串、unicode 转义的键
   会原样保留；`fd_target` / `comm` / 进程 argv（`full_command`）不在脱敏范围内。
2. 跨 TLS 记录拆分的头值、跨两次 read/write 拆分的 stdio 凭据不脱敏
   （无法在不损坏凭据的前提下半改）。
3. 同 uid 进程可读运行库；`0600` 只挡其他用户。
4. Web API 默认无鉴权（TOTP 为可选组件）；请仅绑回环。
5. 不设 CI（ADR-0014）；门禁在本地执行。
6. eBPF 依赖内核版本（下限 5.8）；低版本加载失败必须显式报错，不得降级静默。

## 5. 复核触发器

以下变更必须重读本文并更新：

- 新增任何把 `data_b64` / 自由文本送出本机的路径（导出、上报、webhook）；
- 新增 HTTP 端点或改变绑定地址；
- 改变运行库/日志文件权限；
- 新增 eBPF hook（尤其带指针解引用的 kprobe/fentry）；
- 修改身份解析的信任信号。
