---
status: accepted
date: 2026-10-18
---

# 0008 — 数据出口的安全默认值：无内容导出、出口脱敏、仅回环

## Context

产品的核心能力是捕获**解密后的 agent 明文**，这使「数据出口」成为主威胁面：

- `data_b64` 原样包含 HTTP 头，`Authorization` / `Cookie` 以明文存在；
  旧实现只有 `AuthHeaderRemover` 清洗 `http_parser` 派生事件，原始事件不清洗。
- Web/API 绑定 `0.0.0.0:7395`，且部分端点带 `Access-Control-Allow-Origin: *`：
  操作员访问的任意网页都能从回环读取捕获数据（浏览器可跨域 GET）。
- 运行库由 SQLite 按 umask 创建（常见 0644），同机其他用户可读。
- 没有任何结构化导出面；P5 要求 `export/genai.rs` 且「content 默认不出口」。

## Decision

1. **导出默认无内容**（`collector/src/export/genai.rs`）：
   - scope `io.mtagentlens`、span `mtl.gen_ai.alert`；
   - 默认只导出标识符、枚举、计数、时间戳；
   - 自由文本必须显式 `--include-content`，且经过形状化脱敏；
   - 原始传输字节永不进入 span。
2. **出口脱敏**（`framework/analyzers/raw_secret_redactor.rs`）：
   在任何输出之前重写 `data_b64` 中 HTTP 头的
   `Authorization` / `Proxy-Authorization` / `Cookie` / `Set-Cookie` /
   `X-Api-Key` 值；保留原 `buf_size`，新增 `redaction` 对象记录
   `captured_buf_size` / `redacted_buf_size`，让消费者知道字节已不是原样。
   捕获层（transport）不改字节，byte-lossless 契约仍成立。
3. **仅回环**：默认 `127.0.0.1`；`MTAGENTLENS_BIND` 显式覆盖，非回环时启动打印警告。
4. **无通配 CORS**：删除所有 `Access-Control-Allow-Origin: *`；内嵌前端同源，
   不需要 CORS。
5. **运行库 0600**：可写打开时收紧权限，失败时打印而非静默。

## 残余（显式承认）

- 请求/响应体与工具参数内的自由文本不做通用脱敏（只认 HTTP 头形状）。
- 跨 TLS 记录拆分的头值无法安全脱敏（半改会制造「仍泄露前缀」的损坏凭据）。
- `0600` 只挡其他用户；同 uid 进程（包括被监测 agent）仍可读。
- Web API 无鉴权；TOTP 是可选组件。回环绑定是主要缓解。

## Confirmation

- `export/genai.rs` 4 个测试：默认导出不含任何内容/凭据；显式内容仍被脱敏；
  形状化脱敏表 + 幂等；JSONL 一 span 一行。
- `raw_secret_redactor.rs` 7 个测试：头值替换且其余字节不变、无敏感头字节完全
  不变、非 HTTP/坏 base64 不动、幂等、Set-Cookie、非 ssl 源不动、流式路径。
- `persistence/schema.rs`：可写打开后文件模式为 `0600`（unix 测试）。
- 门禁：`scripts/verify.sh` 13 条全绿；`/home/<user>` 预算 0。
- 文档：`SECURITY.md`、`docs/security/threat-model.md`、
  `docs/security/openSSF-self-assessment.md`。
