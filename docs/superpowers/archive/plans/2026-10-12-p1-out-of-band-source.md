# P1 带外数据源（L2b）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 OpenClaw 自落盘的 `trajectory_runtime_events.tool.call` 接入 MtAgentLens，为 L2 提供 `asserted` 身份（`sessionKey`）、为 P2 提供结构化 Intent（`name` + `args`）。

**Architecture:** 新增一个 **profile 驱动的带外数据源**。它读 agent 自己写下的轨迹文件（不改 agent、不插桩），产出两类事实：`OutOfBandToolCall`（结构化意图）与 `OutOfBandIdentity`（asserted 身份）。这些事实经既有 `IntentExtractor`/`IdentityProvider` 通路汇入责任图，**eBPF 路径保持独立可用**（带外缺失即降级，不失败）。

**Tech Stack:** Rust (edition 2024)、rusqlite（只读）、serde/toml、tokio

## Global Constraints

- Rust 遵循 `cargo fmt` + `cargo clippy`；**禁止** `unsafe` 无 SAFETY 注释
- **只读**访问 agent 落盘数据：`OpenFlags::SQLITE_OPEN_READ_ONLY`，绝不写入
- **eBPF 路径不得被带外路径取代**：带外缺失/版本不匹配时降级到 eBPF，并告警
- 所有带外事实必须标 `provenance = Asserted`（agent 自报）或 `Inferred`
- Claude Code 路径行为冻结
- commit 用中文、无 conventional 前缀、不提 agentsight
- 每个任务结束必须 `cd collector && cargo test` 全绿

---

## 关键既有事实（实现者必读）

| 事实 | 位置 |
|------|------|
| `Intent` 结构与 `extract_intent_args` 认识的字段（含 `path`） | `collector/src/responsibility/types/evidence_types.rs`、`evidence/args.rs:21` |
| `IntentExtractor::extract` 的入口与 SSE fast-path | `evidence/intent_extractor.rs:235` |
| `IdentityProvider` trait（`agent_for_process` / `all_assignments`） | `identity/resolver.rs:30` |
| `AgentId` 是 `newtype_id!` 生成的 `AgentId(pub String)`，无前缀解析 | `responsibility/types/ids.rs` |
| 事件派发入口 | `runtime/routing.rs::dispatch_event` |
| rusqlite 已是依赖 | `collector/Cargo.toml` |

### OpenClaw 带外数据形态（实测，2026-10-12）

库路径：`~/.openclaw/agents/<agentId>/agent/openclaw-agent.sqlite`
表：`trajectory_runtime_events(created_at, event_json, run_id, seq, session_id)`

`tool.call` 记录的 JSON 形态：

```json
{"traceSchema":"openclaw-trajectory","schemaVersion":1,"type":"tool.call",
 "ts":"2026-10-12T06:09:58.375Z",
 "sessionId":"43032fe6-…","sessionKey":"agent:main:main",
 "runId":"1fa1bfe5-…","workspaceDir":"/home/lzk/.openclaw/workspace",
 "provider":"deepseek","modelId":"deepseek-v4-pro","modelApi":"openai-completions",
 "data":{"toolCallId":"tool_call:call_00_…:ls:1","name":"ls",
         "args":{"path":"/home/lzk/MtAgentLens/collector/src/responsibility/runtime"}}}
```

**查询陷阱**：`seq` 是**每 session 独立**的；跨 session 用 `order by seq desc` 会拿到旧 session ⇒ **必须按 `created_at` 排序**。

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `collector/src/responsibility/outofband/mod.rs` | 模块入口 + `OutOfBandSource` trait | Create |
| `collector/src/responsibility/outofband/profile.rs` | profile 配置（TOML）+ 加载 | Create |
| `collector/src/responsibility/outofband/openclaw.rs` | OpenClaw sqlite 读取 + 事件解析 | Create |
| `collector/src/responsibility/outofband/types.rs` | `OutOfBandToolCall` / `OutOfBandIdentity` | Create |
| `collector/src/responsibility/types/evidence_types.rs` | `Intent` 加来源标注 | Modify |
| `collector/src/responsibility/mod.rs` | 注册模块 | Modify |

---

## Task 1: 带外事实类型 + profile 配置骨架

**Files:**
- Create: `collector/src/responsibility/outofband/types.rs`
- Create: `collector/src/responsibility/outofband/profile.rs`
- Create: `collector/src/responsibility/outofband/mod.rs`
- Modify: `collector/src/responsibility/mod.rs`

**Interfaces:**
- Produces:
  - `OutOfBandToolCall { tool_name: String, args: serde_json::Value, agent_key: String, run_id: Option<String>, ts_ns: u64 }`
  - `OutOfBandIdentity { agent_key: String, workspace_dir: Option<String> }`
  - `OutOfBandProfile { agent: String, kind: OutOfBandKind, state_dir: PathBuf, schema_version: u32 }`
  - `OutOfBandKind::{OpenClawTrajectory}`
  - `load_profiles(dir: &Path) -> Result<Vec<OutOfBandProfile>, ProfileError>`

- [ ] **Step 1: 写失败测试**

创建 `collector/src/responsibility/outofband/profile.rs`：

```rust
//! Profile-driven configuration for out-of-band sources.
//!
//! Out-of-band reading is inherently platform-specific, so it is never
//! enabled implicitly: an operator declares which agent's on-disk data may
//! be read, and which schema version was verified against.

use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum OutOfBandKind {
    OpenClawTrajectory,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct OutOfBandProfile {
    /// Agent label this profile applies to (matches the runtime profile id).
    pub agent: String,
    pub kind: OutOfBandKind,
    /// Root directory holding the agent's state (e.g. `~/.openclaw`).
    pub state_dir: PathBuf,
    /// Schema version verified against. A mismatch disables the source
    /// rather than risking misparsed data.
    pub schema_version: u32,
}

#[derive(Debug)]
pub enum ProfileError {
    Io(std::io::Error),
    Toml(toml::de::Error),
}

impl std::fmt::Display for ProfileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "out-of-band profile io error: {e}"),
            Self::Toml(e) => write!(f, "out-of-band profile parse error: {e}"),
        }
    }
}

impl std::error::Error for ProfileError {}

/// Load every `*.toml` in `dir`. A missing directory yields an empty list —
/// out-of-band reading is optional, never a hard requirement.
pub fn load_profiles(dir: &Path) -> Result<Vec<OutOfBandProfile>, ProfileError> {
    if !dir.exists() {
        return Ok(Vec::new());
    }
    let mut out = Vec::new();
    let entries = std::fs::read_dir(dir).map_err(ProfileError::Io)?;
    for entry in entries {
        let path = entry.map_err(ProfileError::Io)?.path();
        if path.extension().and_then(|e| e.to_str()) != Some("toml") {
            continue;
        }
        let text = std::fs::read_to_string(&path).map_err(ProfileError::Io)?;
        out.push(toml::from_str(&text).map_err(ProfileError::Toml)?);
    }
    out.sort_by(|a, b| a.agent.cmp(&b.agent));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_directory_yields_no_profiles() {
        let got = load_profiles(Path::new("/nonexistent/definitely/not/here")).unwrap();
        assert!(got.is_empty());
    }

    #[test]
    fn parses_a_profile() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("openclaw.toml"),
            r#"
agent = "openclaw"
kind = "open-claw-trajectory"
state_dir = "/home/lzk/.openclaw"
schema_version = 1
"#,
        )
        .unwrap();

        let got = load_profiles(dir.path()).unwrap();
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].agent, "openclaw");
        assert_eq!(got[0].kind, OutOfBandKind::OpenClawTrajectory);
        assert_eq!(got[0].schema_version, 1);
    }

    #[test]
    fn ignores_non_toml_files() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("README.md"), "not a profile").unwrap();
        assert!(load_profiles(dir.path()).unwrap().is_empty());
    }
}
```

创建 `collector/src/responsibility/outofband/types.rs`：

```rust
//! Facts recovered from an agent's own on-disk records.
//!
//! These are *asserted* by the agent (it wrote them itself), as opposed to
//! the *inferred* facts derived from eBPF observation. Both are kept: they
//! cross-check each other rather than one replacing the other.

/// A tool invocation the agent recorded for itself.
#[derive(Debug, Clone, PartialEq)]
pub struct OutOfBandToolCall {
    /// Tool name as the agent recorded it, e.g. `ls`.
    pub tool_name: String,
    /// Structured arguments, e.g. `{"path": "/x"}`.
    pub args: serde_json::Value,
    /// Agent key from the record, e.g. `agent:main:main`.
    pub agent_key: String,
    /// Run identifier, when present.
    pub run_id: Option<String>,
    /// Event time in epoch nanoseconds (converted from the record's ISO ts).
    pub ts_ns: u64,
}

/// Identity the agent asserted about itself.
#[derive(Debug, Clone, PartialEq)]
pub struct OutOfBandIdentity {
    /// e.g. `agent:main:main`.
    pub agent_key: String,
    /// Working directory the agent recorded, when present.
    pub workspace_dir: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_call_carries_structured_args() {
        let c = OutOfBandToolCall {
            tool_name: "ls".into(),
            args: serde_json::json!({"path": "/x"}),
            agent_key: "agent:main:main".into(),
            run_id: Some("run-1".into()),
            ts_ns: 1,
        };
        assert_eq!(c.args["path"], "/x");
        assert_eq!(c.agent_key, "agent:main:main");
    }
}
```

创建 `collector/src/responsibility/outofband/mod.rs`：

```rust
//! Out-of-band data sources: facts the agent wrote down itself.
//!
//! Enabled only via an explicit [`profile::OutOfBandProfile`]. The eBPF path
//! remains authoritative and independently usable; these sources add
//! precision where available and degrade silently to nothing where not.

pub mod openclaw;
pub mod profile;
pub mod types;

pub use profile::{OutOfBandKind, OutOfBandProfile, ProfileError, load_profiles};
pub use types::{OutOfBandIdentity, OutOfBandToolCall};
```

- [ ] **Step 2: 注册模块并跑测试**

在 `collector/src/responsibility/mod.rs` 加 `pub mod outofband;`。

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib outofband 2>&1 | tail -12
```

Expected: 4 个测试通过

> 若 `tempfile` 不在 dev-dependencies，改用 `std::env::temp_dir()` + 唯一子目录（先查 `Cargo.toml`）。

- [ ] **Step 3: Commit**

```bash
git add collector/src/responsibility/outofband/ collector/src/responsibility/mod.rs
git commit -m "新增带外数据源的 profile 配置与事实类型"
```

---

## Task 2: OpenClaw 轨迹读取

**Files:**
- Create: `collector/src/responsibility/outofband/openclaw.rs`

**Interfaces:**
- Consumes: `OutOfBandProfile`（Task 1）、`OutOfBandToolCall`/`OutOfBandIdentity`（Task 1）
- Produces:
  - `OpenClawReader::open(profile: &OutOfBandProfile) -> Result<Self, ReadError>`
  - `OpenClawReader::agent_db_path(&self, agent_id: &str) -> PathBuf`
  - `OpenClawReader::read_tool_calls(&self, agent_id: &str) -> Result<Vec<OutOfBandToolCall>, ReadError>`
  - `OpenClawReader::schema_version_ok(&self, agent_id: &str) -> bool`
  - `ReadError::{Io, Sqlite, Schema}`

- [ ] **Step 1: 写失败测试（用内存 SQLite 构造真实表结构）**

创建 `collector/src/responsibility/outofband/openclaw.rs`：

```rust
//! Reader for OpenClaw's trajectory records.
//!
//! Opens the agent database READ-ONLY. Verified schema (2026-10-12):
//! `trajectory_runtime_events(created_at, event_json, run_id, seq, session_id)`.

use super::profile::{OutOfBandKind, OutOfBandProfile};
use super::types::{OutOfBandIdentity, OutOfBandToolCall};
use rusqlite::{Connection, OpenFlags};
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum ReadError {
    Io(std::io::Error),
    Sqlite(rusqlite::Error),
    /// `traceSchema`/`schemaVersion` did not match the profile.
    Schema { found: Option<u32>, expected: u32 },
}

impl std::fmt::Display for ReadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "out-of-band io error: {e}"),
            Self::Sqlite(e) => write!(f, "out-of-band sqlite error: {e}"),
            Self::Schema { found, expected } => write!(
                f,
                "out-of-band schema mismatch: found {found:?}, expected {expected}"
            ),
        }
    }
}

impl std::error::Error for ReadError {}

pub struct OpenClawReader {
    state_dir: PathBuf,
    expected_schema: u32,
}

impl OpenClawReader {
    pub fn open(profile: &OutOfBandProfile) -> Result<Self, ReadError> {
        if profile.kind != OutOfBandKind::OpenClawTrajectory {
            return Err(ReadError::Schema {
                found: None,
                expected: profile.schema_version,
            });
        }
        Ok(Self {
            state_dir: profile.state_dir.clone(),
            expected_schema: profile.schema_version,
        })
    }

    /// `<state_dir>/agents/<agent_id>/agent/openclaw-agent.sqlite`
    pub fn agent_db_path(&self, agent_id: &str) -> PathBuf {
        self.state_dir
            .join("agents")
            .join(agent_id)
            .join("agent")
            .join("openclaw-agent.sqlite")
    }

    fn connect(&self, agent_id: &str) -> Result<Connection, ReadError> {
        let path = self.agent_db_path(agent_id);
        Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(ReadError::Sqlite)
    }

    /// True when the newest event's `schemaVersion` matches the profile.
    pub fn schema_version_ok(&self, agent_id: &str) -> bool {
        let Ok(conn) = self.connect(agent_id) else {
            return false;
        };
        let Ok(json) = conn.query_row(
            "SELECT event_json FROM trajectory_runtime_events \
             WHERE event_json IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            [],
            |r| r.get::<_, String>(0),
        ) else {
            return false;
        };
        let Ok(v) = serde_json::from_str::<serde_json::Value>(&json) else {
            return false;
        };
        v.get("schemaVersion").and_then(|x| x.as_u64()) == Some(self.expected_schema as u64)
            && v.get("traceSchema").and_then(|x| x.as_str()) == Some("openclaw-trajectory")
    }

    /// Read every recorded tool call, oldest first.
    ///
    /// Ordering is by `created_at` — NOT `seq`, which is per-session and would
    /// silently return an older session's rows.
    pub fn read_tool_calls(&self, agent_id: &str) -> Result<Vec<OutOfBandToolCall>, ReadError> {
        let conn = self.connect(agent_id)?;
        let mut stmt = conn
            .prepare(
                "SELECT event_json FROM trajectory_runtime_events \
                 WHERE event_json LIKE '%\"tool.call\"%' ORDER BY created_at ASC",
            )
            .map_err(ReadError::Sqlite)?;

        let rows = stmt
            .query_map([], |r| r.get::<_, String>(0))
            .map_err(ReadError::Sqlite)?;

        let mut out = Vec::new();
        for row in rows {
            let json = row.map_err(ReadError::Sqlite)?;
            if let Some(call) = parse_tool_call(&json) {
                out.push(call);
            }
        }
        Ok(out)
    }
}

/// Parse one `tool.call` record. Returns `None` for other event types and for
/// records missing the fields we require.
fn parse_tool_call(json: &str) -> Option<OutOfBandToolCall> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    if v.get("type").and_then(|x| x.as_str()) != Some("tool.call") {
        return None;
    }
    let data = v.get("data")?;
    let tool_name = data.get("name").and_then(|x| x.as_str())?.to_string();
    let args = data.get("args").cloned().unwrap_or(serde_json::Value::Null);
    let agent_key = v.get("sessionKey").and_then(|x| x.as_str())?.to_string();
    let run_id = v.get("runId").and_then(|x| x.as_str()).map(str::to_string);
    let ts_ns = v
        .get("ts")
        .and_then(|x| x.as_str())
        .and_then(iso8601_to_epoch_ns)
        .unwrap_or(0);

    Some(OutOfBandToolCall {
        tool_name,
        args,
        agent_key,
        run_id,
        ts_ns,
    })
}

/// Convert an ISO-8601 UTC timestamp (`2026-10-12T06:09:58.375Z`) to epoch ns.
fn iso8601_to_epoch_ns(ts: &str) -> Option<u64> {
    let (date, rest) = ts.split_once('T')?;
    let rest = rest.trim_end_matches('Z');
    let (time, frac) = match rest.split_once('.') {
        Some((t, f)) => (t, f),
        None => (rest, ""),
    };
    let mut d = date.split('-');
    let year: i64 = d.next()?.parse().ok()?;
    let month: i64 = d.next()?.parse().ok()?;
    let day: i64 = d.next()?.parse().ok()?;
    let mut t = time.split(':');
    let hour: i64 = t.next()?.parse().ok()?;
    let minute: i64 = t.next()?.parse().ok()?;
    let second: i64 = t.next()?.parse().ok()?;

    // Days since Unix epoch (Howard Hinnant's civil-from-days, inverted).
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;

    let secs = days * 86400 + hour * 3600 + minute * 60 + second;
    if secs < 0 {
        return None;
    }
    let frac_ns: u64 = if frac.is_empty() {
        0
    } else {
        let mut f = frac.to_string();
        f.truncate(9);
        while f.len() < 9 {
            f.push('0');
        }
        f.parse().ok()?
    };
    Some(secs as u64 * 1_000_000_000 + frac_ns)
}

/// Identity asserted by the agent, derived from any tool call record.
pub fn identity_from_call(call: &OutOfBandToolCall, workspace_dir: Option<String>) -> OutOfBandIdentity {
    OutOfBandIdentity {
        agent_key: call.agent_key.clone(),
        workspace_dir,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

    const CALL_JSON: &str = r#"{"traceSchema":"openclaw-trajectory","schemaVersion":1,"type":"tool.call","ts":"2026-10-12T06:09:58.375Z","sessionKey":"agent:main:main","runId":"run-1","data":{"toolCallId":"x","name":"ls","args":{"path":"/tmp/x"}}}"#;

    fn make_db(path: &Path) {
        let conn = Connection::open(path).unwrap();
        conn.execute_batch(
            "CREATE TABLE trajectory_runtime_events (
                 session_id TEXT, seq INTEGER, run_id TEXT,
                 event_json TEXT, created_at INTEGER);",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO trajectory_runtime_events VALUES (?1,?2,?3,?4,?5)",
            rusqlite::params!["s1", 1, "run-1", CALL_JSON, 100i64],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO trajectory_runtime_events VALUES (?1,?2,?3,?4,?5)",
            rusqlite::params![
                "s1",
                2,
                "run-1",
                r#"{"traceSchema":"openclaw-trajectory","type":"session.started"}"#,
                200i64
            ],
        )
        .unwrap();
    }

    fn reader_for(dir: &Path) -> OpenClawReader {
        OpenClawReader {
            state_dir: dir.to_path_buf(),
            expected_schema: 1,
        }
    }

    #[test]
    fn parses_iso8601_to_epoch_ns() {
        // 2026-10-12T06:09:58.375Z
        let got = iso8601_to_epoch_ns("2026-10-12T06:09:58.375Z").unwrap();
        assert_eq!(got % 1_000_000_000, 375_000_000, "fractional part preserved");
        assert_eq!(got / 1_000_000_000, 1791785398, "whole seconds match");
    }

    #[test]
    fn parses_tool_call_fields() {
        let c = parse_tool_call(CALL_JSON).expect("should parse");
        assert_eq!(c.tool_name, "ls");
        assert_eq!(c.args["path"], "/tmp/x");
        assert_eq!(c.agent_key, "agent:main:main");
        assert_eq!(c.run_id.as_deref(), Some("run-1"));
        assert!(c.ts_ns > 0);
    }

    #[test]
    fn ignores_non_tool_call_events() {
        let other = r#"{"traceSchema":"openclaw-trajectory","type":"session.started"}"#;
        assert!(parse_tool_call(other).is_none());
    }

    #[test]
    fn reads_tool_calls_from_db() {
        let dir = tempfile::tempdir().unwrap();
        let db_dir = dir.path().join("agents").join("main").join("agent");
        std::fs::create_dir_all(&db_dir).unwrap();
        make_db(&db_dir.join("openclaw-agent.sqlite"));

        let r = reader_for(dir.path());
        let calls = r.read_tool_calls("main").unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].tool_name, "ls");
    }

    #[test]
    fn missing_db_is_an_error_not_a_panic() {
        let dir = tempfile::tempdir().unwrap();
        let r = reader_for(dir.path());
        assert!(r.read_tool_calls("nope").is_err());
        assert!(!r.schema_version_ok("nope"));
    }
}
```

- [ ] **Step 2: 跑测试**

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib outofband::openclaw 2>&1 | tail -15
```

Expected: 5 个测试通过

> 若有失败，先核对 `iso8601_to_epoch_ns` 的期望值：可用 `date -u -d '2026-10-12T06:09:58Z' +%s` 反查。

- [ ] **Step 3: Commit**

```bash
git add collector/src/responsibility/outofband/openclaw.rs
git commit -m "新增 OpenClaw 轨迹读取器，只读访问并解析结构化工具调用"
```

---

## Task 3: 真实环境端到端验证

**Files:** 无（验证任务）

**Interfaces:**
- Consumes: Task 2 的 `OpenClawReader`

- [ ] **Step 1: 写一次性验证（bin 或 test）**

用 `cargo test -- --ignored` 形式加一个 `#[ignore]` 的真实环境测试：读本机 `~/.openclaw`，断言能读出 `ls` 调用与 `agent:main:main`。

```rust
#[test]
#[ignore = "requires a live OpenClaw installation"]
fn reads_real_openclaw_state() {
    let home = std::env::var("HOME").unwrap();
    let profile = OutOfBandProfile {
        agent: "openclaw".into(),
        kind: OutOfBandKind::OpenClawTrajectory,
        state_dir: PathBuf::from(format!("{home}/.openclaw")),
        schema_version: 1,
    };
    let r = OpenClawReader::open(&profile).unwrap();
    assert!(r.schema_version_ok("main"), "schema 应与 profile 匹配");
    let calls = r.read_tool_calls("main").unwrap();
    assert!(!calls.is_empty(), "应至少读到一次工具调用");
    assert!(
        calls.iter().all(|c| c.agent_key.starts_with("agent:")),
        "每条记录都应带 agent 键"
    );
}
```

- [ ] **Step 2: 运行**

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib reads_real_openclaw_state -- --ignored --nocapture 2>&1 | tail -12
```

Expected: PASS（本机存在 OpenClaw 状态与历史 tool.call）

- [ ] **Step 3: 记录结果**

把读到的工具名分布、agent_key 分布写进本计划的「验证记录」小节。

---

## 验证记录（2026-10-12 完成）

**Task 1/2/3 全部完成。**

| 验收项 | 结果 |
|--------|------|
| 单元测试 | **9 passed**（profile 解析 3 + 类型 1 + OpenClaw 读取 5） |
| 真实环境（`reads_real_openclaw_state`） | **PASS** |
| 全量回归 | **903 passed, 0 failed** |
| clippy | 无新增告警 |

**真实数据统计**（本机 `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`）：

```
工具名分布: {'exec': 6, 'memory_search': 1, 'ls': 1}
agent 键分布: {'agent:main:main': 8}      ← asserted 身份，8/8 条记录全部携带
带结构化参数的调用: 8/8 (100%)
```

**结论**：带外读取通路打通且数据质量符合预期——**每条记录都同时具备 `asserted` agent 键与结构化参数**，为后续 P2（意图注入）与 L2（身份注入）提供了直接可用的输入。

**实现偏差记录**：
1. `profile.rs` 的 `Vec::new()` 需要显式类型标注（`Vec<OutOfBandProfile>`），编译器无法从 `toml::from_str` 推断。
2. 额外新增了 `profile_for()` 便捷构造器（计划里没有），供真实环境测试使用。
3. `identity_from_call()` 已实现但尚未接入链路（属后续 Task 4）。

---

## 后续（不在本计划范围）

- **Task 4+**：把 `OutOfBandToolCall` 转成 `Intent` 并按 `provenance = Asserted` 注入 `IntentExtractor` 通路（属 P2）
- **Task 5+**：把 `agent_key` 接成 `IdentityProvider` 的 `asserted` 来源（属 L2）
- **L1 运行时身份**（`RuntimeResolver` + 消除 `AGENT_COMMS` 双副本）独立成计划

---

## Self-Review 记录

- **Spec 覆盖**：本计划覆盖 spec §5 L2b 的「读取 + 解析 + 校验」部分；Intent/Identity 注入（§5 L2b 设计约束 1–4 的落地）留待后续计划，已在「后续」显式列出。
- **占位符扫描**：无 TBD/TODO；每个代码步骤含可粘贴代码。
- **类型一致性**：`OutOfBandToolCall`（Task 1 定义）在 Task 2 的 `read_tool_calls` 与测试中同名同字段；`OutOfBandProfile.schema_version` 与 `OpenClawReader.expected_schema` 对应。
- **已知缺口**：`tempfile` 是否在 dev-dependencies 未核实，Step 2 给了替代方案。
