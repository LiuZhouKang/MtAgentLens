# 安全策略

MtAgentLens 在内核层捕获解密后的 agent 明文流量。它看到的内容本身就是敏感数据，
因此安全默认值是产品的一部分，不是附加项。

## 报告漏洞

请通过项目托管平台（GitLink / GitLab）的**私密 issue / confidential issue** 报告，
不要公开披露可利用细节。维护者会在 7 天内确认收到，并在修复可用后与你协调披露时间。
报告中请包含：受影响版本（`mtagentlens --version` 或 commit）、内核版本、复现步骤、
以及是否已在真实 agent 上观察到数据泄露。

## 支持范围

| 版本 | 状态 |
|---|---|
| 当前 `main` | 接收安全修复 |
| 更早的提交 | 不保证；请先复现于当前 `main` |

## 安全默认值（当前实现）

- **只监听回环**：Web/API 默认绑定 `127.0.0.1:7395`。远程访问必须显式设置
  `MTAGENTLENS_BIND`；绑定非回环地址时启动会打印警告（`src/main.rs::start_web_server_if_enabled`）。
- **无通配 CORS**：REST/SSE 响应不发送 `Access-Control-Allow-Origin: *`，
  防止操作员访问的任意网页经浏览器读取回环明文。
- **运行库权限**：可写打开时把数据库文件收紧为 `0600`（`persistence/schema.rs::restrict_file_permissions`）。
- **原始明文脱敏**：`ssl` 事件的 `data_b64` 在进入输出前由
  `framework/analyzers/raw_secret_redactor.rs` 重写 HTTP 头中的
  `Authorization` / `Proxy-Authorization` / `Cookie` / `Set-Cookie` /
  `X-Api-Key` 值，并在事件中留下 `redaction` 标记。捕获本身不被修改。
- **stdio 载荷脱敏**：MCP 的 stdin/stdout 事件在进入控制台、日志文件或 SSE 前，
  由 `framework/analyzers/stdio_secret_redactor.rs` 对 `data` 应用
  `export::genai::redact` 的形状化规则（Authorization/Bearer、`key=value`
  凭据、Anthropic/OpenAI/AWS/GitHub 前缀），并记录 `redaction`
  （`captured_data_bytes` / `redacted_data_bytes`）。捕获本身不被修改。
- **导出无内容**：`mtagentlens export-genai` 默认只导出标识符、枚举、计数与时间戳；
  自由文本需显式 `--include-content`，且仍会经过形状化脱敏
  （`export/genai.rs`）。
- **认证**：Web UI 的 TOTP 2FA 模块（`collector/src/auth/`）为可选部署组件；
  API 本身默认无鉴权——请把它当作只监听回环的本地工具使用。

## 已知残余风险（请按此评估部署）

1. **请求/响应体与工具参数只做形状化脱敏**。SSL body 不做通用脱敏；stdio `data`
   按已知凭据形状重写：JSON 输入进入 JSON 感知模式（对象成员、转义 JSON 字符串、
   敏感键下的数组/数值、字符串内的 header/label 文本，只替换值、保持 JSON 有效）；
   非 JSON 输入用 header 与 `key=value` 规则。没有可识别前缀或键名的随机串、
   用 JSON unicode 转义写的键（`password`）会原样保留；
   `fd_target` / `comm` / 进程 argv 不在脱敏范围内。原始库与日志仍应按敏感数据处理。
2. **跨记录/跨事件拆分的凭据不会被脱敏**：TLS 头跨记录、stdio 凭据跨两次
   read(2)/write(2) 时，单个事件只含半个值，无法在不制造损坏凭据的前提下重写。
   SSL 完整消息由 `PayloadNormalizer` 组装后才会命中；stdio 暂无重组缓冲。
3. **同用户进程可读**：`0600` 只阻止其他用户；与 collector 同 uid 的进程
   （包括被监测的 agent）可以读运行库。不要把运行目录放在共享位置。
4. **collector 以高权限运行**：eBPF 需要 `CAP_BPF` + `CAP_PERFMON`（或 root）。
   请只运行本仓库构建的二进制，并核对 `scripts/verify.sh` 全绿。
5. **无 CI/CD 依赖**：唯一门禁是 `scripts/verify.sh`（14 条，离线）；
   网络相关的依赖审计是独立命令 `python3 scripts/audit_deps.py all`。
   发布前在构建主机上依次运行二者。仓库不再携带 CI 配置或 runner 脚本。

## 凭据泄露处置（2026-10-19）

历史提交曾把 `env/config/vendors/*/observer.toml` 与 `claude.env`（含真实
`sk-...` API key）纳入版本控制。本次清理已：

- 将上述真实配置文件从索引移除，并加入 `.gitignore`（只保留 `*.example` 模板）；
- 修正 `observer.toml.example` 为当前 flat schema。

**仅移除索引不能撤回历史中的值。** 曾经推送过的 key 必须视为已泄露：
请立刻在 DeepSeek / Anthropic 控制台**轮换**这些 key，并检查账单。
需要彻底清除历史时使用 `git filter-repo`；轮换优先于改写历史。

威胁模型与逐项缓解见 [`docs/security/threat-model.md`](docs/security/threat-model.md)。
