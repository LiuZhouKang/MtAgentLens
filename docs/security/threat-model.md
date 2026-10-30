# 威胁模型与数据边界

MtAgentLens 以较高权限观察不可信进程，处理可能包含秘密的明文，再生成证据和裁决。本说明区分代码中的防护与残余风险；测试通过不等于隔离、匿名化或任意内核兼容保证。漏洞报告与部署要求见 [SECURITY.md](../../SECURITY.md)。

## 资产与信任边界

| 资产或边界 | 风险 |
|---|---|
| TLS 明文、stdio、进程 argv、文件路径 | 可能含任务正文、凭据和个人信息 |
| 运行 SQLite、日志、导出和浏览器响应 | 是捕获内容的副本；持久化后仍需访问控制与保留策略 |
| 身份、责任链和裁决 | 关联错误或缺失事件可能改变归属；结论必须带来源和权限级别 |
| eBPF helper 与 Collector 二进制 | 以 root/capabilities 运行，被替换会扩大主机风险 |
| Observer 请求 | 配置远端 provider 后，分析内容会离开本机 |

数据从被观察进程经内核 hook、ring buffer 和用户态 helper 进入 Collector。捕获内容仍是不可信数据；进程名、消息角色或模型文本不能充当用户认证。Collector 再跨越磁盘、本机 HTTP 和可选远端 Observer 边界。

## 身份与证据完整性

[进程采集器](../../bpftools/process/process.bpf.c) 与 [ProcessId](../../collector/src/responsibility/types/ids.rs) 使用进程代际信息，避免只用 PID 关联。身份解析还使用 TLS、conversation 及消息绑定，并受 [冻结身份策略](../../collector/src/responsibility/identity/policy.rs) 约束。它提供观测关联，不阻止恶意进程伪装名称，也不证明模型声明的角色真实。

上下文共享的授权条件只覆盖直接观测到的进程委派；传递委派、角色声明和能力文本不能直接扩展授权。边界见 [ADR-0015](../adr/0015-alert-actionability-and-taint-authorization.md)。

[最终裁决](../../collector/src/responsibility/classification/final_decision.rs) 区分硬动作证据、按候选绑定的语义复核和未解决状态。分诊高分不是违规证明，Safe 也不是所有行为安全的保证。缺失、截断和来源丢失会限制结论，不能用重放或推断补成真实捕获事实。

## 磁盘与回放

[数据库入口](../../collector/src/responsibility/persistence/schema.rs) 先分类库类型再配置写入；拒绝未知、异类或未来版本，迁移以事务执行，可写文件收紧为 `0600`。这只限制不同 Unix 用户，不能隔离同 uid、root 或已获得文件访问权的进程；也不代表目录、所有日志和导出都具有相同权限。

[AnalysisCheckpoint](../../collector/src/responsibility/replay/checkpoint.rs) 保存回放契约、分析输入与实现指纹，用于发现不兼容分析。它不是签名或防篡改存储；能修改数据及其清单的本机攻击者仍可破坏证据。运行库和冻结实验原件应独立保留，派生分析不能覆盖来源。

## 脱敏与展示

| 通路 | 当前防护 | 未覆盖范围 |
|---|---|---|
| TLS 原始事件 | [RawSecretRedactor](../../collector/src/framework/analyzers/raw_secret_redactor.rs) 在后续输出前重写可识别的敏感头，附脱敏诊断 | 内核捕获本身仍有原字节；正文不是通用脱敏对象 |
| stdio | [StdioSecretRedactor](../../collector/src/framework/analyzers/stdio_secret_redactor.rs) 对已知凭据形状及敏感键做结构化脱敏 | 未识别随机串、跨事件拆分值和其他进程元数据可能保留 |
| GenAI 导出 | [ExportPolicy](../../collector/src/export/genai.rs) 默认控制内容输出；显式 include-content 仍经过既有脱敏 | 不构成匿名化，也不控制 Observer 请求 |
| Web | 默认回环绑定，未启用通配 CORS | 无内置鉴权；同机进程可直接请求 API，回环不等于私有会话 |

链的实际顺序见 [main.rs](../../collector/src/main.rs)。脱敏仅作用于接入该 analyzer 的路径，不能推广到直接运行 helper、历史原始记录或其他离线副本。`fd_target`、`comm`、argv 等自由文本也可能泄露秘密；跨 TLS 记录或 stdio read/write 拆分的内容没有完整脱敏保证。

服务默认绑定 `127.0.0.1:7395`，`MTAGENTLENS_BIND` 可改变地址，源码会提示非回环绑定。远程访问需要部署方提供认证、加密及访问控制；不要直接公开原始事件或运行库接口。

## Observer 的外发通路

[Observer provider](../../collector/src/responsibility/observer/provider.rs) 向配置的 Anthropic-compatible `/v1/messages` 发送请求。输入包括候选资源/动作、结果，以及 [ObservedTaskContext](../../collector/src/responsibility/observer/types.rs)：最多 8 KiB、16 条消息，并记录 hash 和截断/遗漏标志。

任务上下文来自捕获的请求；响应及缺少请求 framing 的内容不能直接提升为用户任务。TLS 中的 `role=user` 仍不是认证。任务正文与资源名可能敏感，大小限制不是脱敏，`export-genai --include-content` 也不控制这一出口。部署前应确认 endpoint、凭据、可发送的数据与 provider 保留策略；离线导出开关不能替代此项配置。

## 资源耗尽与高权限执行

采集器使用过滤、容量边界和 dropped-events 计数处理高频事件；规范化层记录解码失败与 partial 状态。它们改善可诊断性，不保证持续过载下不丢失、无 OOM 或无观测盲区。离线吞吐/RSS 测试只证明指定测试负载，不能作为任意生产流量的资源上限。

BPF 程序受 verifier 限制，但 verifier 通过不证明捕获语义正确。用户态加载器、TLS 库 hook、指针读取和目标内核都需检查。Linux 5.8/ring buffer、BTF 与相应权限只是前置条件，平台兼容须在目标环境实际验证。源码位置为 `bpftools/process/`、`tls_capture/`、`stdiocap/`；构建/加载条件见 [采集说明](../../bpftools/README.md)。

## 变更时需要重新检查的边界

- 新增自由文本外发、导出、日志或遥测：明确字段、脱敏位置、目的地和权限。
- 改 HTTP 端点、绑定地址或静态资源路径：检查鉴权假设、来源限制与路径穿越。
- 改 DB 迁移、备份或文件创建：确认分类先于写入，保护原库与权限。
- 改身份信号、委派授权或最终裁决：区分观测事实、声明与推断，验证作用域和代际。
- 改 BPF hook、缓冲容量或解析边界：实际加载并记录内核与丢失诊断。

现有工程门禁与冻结实验分别见 [贡献指南](../../CONTRIBUTING.md) 和 [当前证据入口](../evaluation/CURRENT-EVIDENCE.md)。两者都不能代替上述残余风险说明。
