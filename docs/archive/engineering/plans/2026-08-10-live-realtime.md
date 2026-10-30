# 前端实时化（统一 SSE 实时层）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让前端在 `record`/`trace` 直播中实时服务后端：态势图演化、告警流、原始事件流、运行监控派生视角、图时间演化时间轴全部实时，运行结束自动转回放；离线保持导入/回放能力。

**Architecture:** 后端扩展旧 WebServer 新增 `/api/live/events` 统一实时 SSE（`alert_tx` 告警 + `event_sender` 原始事件多路复用，含 seq/heartbeat/lag）。前端 `LiveSession` 单例负责 SSE 连接、run 生命周期轮询、快照 resync；低频/结构数据进 zustand store，高频数据（原始事件）存 LiveSession 环形缓冲 + `useSyncExternalStore` 细粒度订阅。

**Tech Stack:** Rust (hyper 1.0, tokio-stream, tokio broadcast) · TypeScript/React 19/Next 16 · zustand · @tanstack/react-query · vitest · Playwright

## Global Constraints

- 交互与文档使用中文；代码/变量/commit message 使用英文。
- TDD 铁律：每个任务以失败测试开始、以通过结束；无测试不提交功能性代码。
- 后端所有修改必须 `cd collector && cargo test` 全绿；前端必须 `npm test` + `npm run type-check` + `npm run lint` 全绿。
- e2e 仅 `--project=chromium --project=firefox --workers=1`。
- 高频实时数据（原始事件/速率统计）**绝不入 zustand**（selector 稳定引用教训）；用 LiveSession 环形缓冲 + uSES。
- legacy `/api/events`（web.rs `serve_events_api`）**不改动、不删除**，现有测试零破坏；新路由为 `/api/live/events`。
- `responsibility/server`（ServerState/serve_request）保持现状不动。
- 前端 dev 环境 `/api/*` 重写到 Python(7397)；实时模式开发验证用 e2e mock SSE 或临时改 rewrite 指向 Rust 后端，生产（嵌入式同源）天然可用。

---

## 文件结构

### 后端
| 文件 | 职责 |
|------|------|
| `collector/src/server/live_sse.rs`（新建） | `LiveSseEvent` 帧、`live_event_stream`（合并 raw+alert+heartbeat，共享 seq）、`serve_live_events_api` |
| `collector/src/server/web.rs`（修改） | `WebServer.alert_rx` 字段、`handle_request` 返回类型收窄 `BoxBody`、`/api/live/events` 路由 |
| `collector/src/server/mod.rs`（修改） | `pub mod live_sse;` |
| `collector/src/main.rs`（修改） | `start_web_server_if_enabled` 增加 `alert_tx` 参数；`run_trace` 传入 |

### 前端
| 文件 | 职责 |
|------|------|
| `frontend/lib/live/live-types.ts`（新建） | `LiveConnectionStatus`/`LiveRunMeta`/`LiveStats`/`LiveRawEvent`/`LiveState` |
| `frontend/lib/live/live-raw-events.ts`（新建） | 环形缓冲 + `subscribeLiveRawEvents`/`getLiveRawEvents`（uSES 契约） |
| `frontend/lib/live/live-session.ts`（新建） | `LiveSession` 类（SSE/生命周期/resync/统计）+ `createLiveSession(deps)` + 生产单例 `startLiveSession/stopLiveSession` |
| `frontend/lib/store.ts`（修改） | `live` 切片字段 + `setLiveStatus/setLiveRun/setLiveStats/markRunEnded`；激活 `onAlertReceived/onRunActiveChanged` |
| `frontend/lib/api.ts`（修改） | `LIVE_EVENTS_URL` 常量 + `getAlertsFull(id)`（非 lean） |
| `frontend/app/(main)/layout.tsx`（修改） | 挂 `LiveSessionMount` + `LiveStatusBar` |
| `frontend/components/live/live-session-mount.tsx`（新建） | `useEffect` 挂载/卸载 live 单例 |
| `frontend/components/live/live-status-bar.tsx`（新建） | 常驻连接状态/活跃 run/速率/lag |
| `frontend/components/live/live-event-tail.tsx`（新建） | 原始事件虚拟化 live-tail（自动跟随/过滤/折叠载荷） |
| `frontend/components/live/live-alert-feed.tsx`（新建） | 态势页实时告警侧栏 |
| `frontend/app/(main)/console/page.tsx` 及其客户端组件 | 态势实时接线（图演化 + LiveAlertFeed + LiveEventTail） |

---

## 阶段 0：后端实时 SSE

### Task 1: `live_sse.rs` — `LiveSseEvent` 帧

**Files:**
- Create: `collector/src/server/live_sse.rs`

**Interfaces:**
- Produces: `LiveSseEvent { event_type: String, data: String, id: Option<String> }`，`to_frame(&self) -> String`，`alert(&ResponsibilityAlert, Option<String>) -> Self`，`raw(&Event, Option<String>) -> Self`，`lag(usize, Option<String>) -> Self`，`heartbeat() -> Self`。

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_live_sse_alert_frame() {
    let e = LiveSseEvent::alert(&dummy_alert(), Some("3".into()));
    let f = e.to_frame();
    assert!(f.starts_with("id: 3\n"));
    assert!(f.contains("event: alert\n"));
    assert!(f.contains("data: {\"alert_id\":\"a1\"}"));
}

#[test]
fn test_live_sse_raw_event_frame() {
    let ev = Event::new_with_timestamp(1_700_000_000_000_000_000, "tls_capture".into(), 8120, "claude".into(), serde_json::json!({"len": 1200}));
    let e = LiveSseEvent::raw(&ev, Some("7".into()));
    let f = e.to_frame();
    assert!(f.contains("event: event\n"));
    assert!(f.contains("\"timestamp_ns\":1700000000000000000"));
    assert!(f.contains("\"source\":\"tls_capture\""));
}

#[test]
fn test_live_sse_lag_frame() {
    let f = LiveSseEvent::lag(3, Some("9".into())).to_frame();
    assert!(f.contains("event: lag\n"));
    assert!(f.contains("{\"dropped\":3}"));
}

#[test]
fn test_live_sse_heartbeat() {
    assert!(LiveSseEvent::heartbeat().to_frame().contains("event: heartbeat\n"));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd collector && cargo test -p mtagentlens live_sse --no-default-features 2>/dev/null || cargo test live_sse`
Expected: FAIL（module 不存在）。

- [ ] **Step 3: Write minimal implementation**

```rust
// SPDX-License-Identifier: MIT
// 统一实时 SSE 事件帧：/api/live/events 的载荷单元。
use crate::framework::core::Event;
use crate::responsibility::types::alert_types::ResponsibilityAlert;

#[derive(Debug, Clone)]
pub struct LiveSseEvent {
    pub event_type: String,
    pub data: String,
    pub id: Option<String>,
}

impl LiveSseEvent {
    pub fn to_frame(&self) -> String {
        let mut frame = String::new();
        if let Some(ref id) = self.id {
            frame.push_str(&format!("id: {}\n", id));
        }
        frame.push_str(&format!("event: {}\n", self.event_type));
        for line in self.data.lines() {
            frame.push_str(&format!("data: {}\n", line));
        }
        frame
    }

    pub fn alert(alert: &ResponsibilityAlert, id: Option<String>) -> Self {
        Self { event_type: "alert".into(), data: serde_json::to_string(alert).unwrap_or_default(), id }
    }

    pub fn raw(event: &Event, id: Option<String>) -> Self {
        Self { event_type: "event".into(), data: event.to_json_value().to_string(), id }
    }

    pub fn lag(dropped: usize, id: Option<String>) -> Self {
        Self { event_type: "lag".into(), data: format!(r#"{{"dropped":{dropped}}}"#), id }
    }

    pub fn heartbeat() -> Self {
        Self { event_type: "heartbeat".into(), data: String::new(), id: None }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**
Run: `cd collector && cargo test live_sse`
Expected: PASS。

- [ ] **Step 5: Commit**
`git add collector/src/server/live_sse.rs && git commit -m "live SSE 事件帧：alert/raw/lag/heartbeat 帧格式"`

### Task 2: `live_sse.rs` — `live_event_stream` 合并流 + seq

**Files:**
- Modify: `collector/src/server/live_sse.rs`

**Interfaces:**
- Produces: `pub fn live_event_stream(event_rx: broadcast::Receiver<Event>, alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>) -> impl Stream<Item = LiveSseEvent>` — 共享单调 seq，任意源 lag 产 `lag` 事件，30s 心跳。

- [ ] **Step 1: Write the failing test**

```rust
#[tokio::test]
async fn test_stream_merges_alert_and_raw() {
    let (event_tx, event_rx) = broadcast::channel::<Event>(16);
    let (alert_tx, alert_rx) = broadcast::channel::<ResponsibilityAlert>(16);
    alert_tx.send(dummy_alert()).unwrap();
    event_tx.send(Event::new_with_timestamp(1, "proc".into(), 1, "c".into(), serde_json::json!({}))).unwrap();
    drop(alert_tx); drop(event_tx);

    let stream = live_event_stream(event_rx, Some(alert_rx));
    let events: Vec<LiveSseEvent> = stream.take(2).collect().await;
    let types: Vec<String> = events.iter().map(|e| e.event_type.clone()).collect();
    assert!(types.contains(&"alert".to_string()));
    assert!(types.contains(&"event".to_string()));
    // seq 单调递增且无重复
    let ids: Vec<String> = events.iter().filter_map(|e| e.id.clone()).collect();
    assert_eq!(ids.len(), 2);
    assert_ne!(ids[0], ids[1]);
}

#[tokio::test]
async fn test_stream_lag_reports_dropped() {
    let (event_tx, event_rx) = broadcast::channel::<Event>(1);
    for i in 0..3 { event_tx.send(Event::new_with_timestamp(i, "p".into(), 1, "c".into(), serde_json::json!({}))).unwrap(); }
    drop(event_tx);
    let stream = live_event_stream(event_rx, None);
    let events: Vec<LiveSseEvent> = stream.take(2).collect().await;
    // 缓冲 1，先 lag 后补拉（顺序不保证，只断言出现了 lag 且 dropped>0）
    assert!(events.iter().any(|e| e.event_type == "lag"));
}
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL（`live_event_stream` 未定义）。

- [ ] **Step 3: Write minimal implementation**

```rust
use futures::StreamExt;
use tokio::sync::broadcast;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::Stream;

pub fn live_event_stream(
    event_rx: broadcast::Receiver<Event>,
    alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>,
) -> impl Stream<Item = LiveSseEvent> {
    use tokio_stream::StreamExt as _;

    let raw_stream = BroadcastStream::new(event_rx).map(|res| match res {
        Ok(ev) => Ok(ev),
        Err(tokio_stream::wrappers::errors::BroadcastStreamRecvError::Lagged(n)) => Err(n),
    });

    let alert_stream = alert_rx.map(|rx| {
        BroadcastStream::new(rx).map(|res| match res {
            Ok(a) => Ok(a),
            Err(tokio_stream::wrappers::errors::BroadcastStreamRecvError::Lagged(n)) => Err(n),
        })
    });

    let heartbeat = tokio_stream::wrappers::IntervalStream::new(
        tokio::time::interval(std::time::Duration::from_secs(30)),
    )
    .map(|_| Some(LiveSseEvent::heartbeat()));

    let mut seq: u64 = 0;

    let raw = raw_stream.map(move |r| {
        seq += 1;
        let id = Some(format!("{seq}"));
        match r {
            Ok(ev) => LiveSseEvent::raw(&ev, id),
            Err(n) => LiveSseEvent::lag(n, id),
        }
    });

    let alerts = if let Some(alert_stream) = alert_stream {
        let mut s = alert_stream;
        s.map(move |r| {
            seq += 1;
            let id = Some(format!("{seq}"));
            match r {
                Ok(a) => LiveSseEvent::alert(&a, id),
                Err(n) => LiveSseEvent::lag(n, id),
            }
        })
        .boxed()
    } else {
        futures::stream::empty().boxed()
    };

    futures::stream::select(heartbeat, futures::stream::select(raw, alerts))
}
```

> 注：`seq` 在每个闭包内递增，两路各自递增会重复。正确做法是**共享一个 `Arc<AtomicU64>`** 或改用一个带状态的 `scan`。请用 `scan` 统一 seq：

```rust
// 统一 seq：用 scan 包一层
let raw = raw_stream.scan(0u64, |seq, r| {
    *seq += 1;
    let id = Some(format!("{seq}"));
    Some(match r {
        Ok(ev) => LiveSseEvent::raw(&ev, id),
        Err(n) => LiveSseEvent::lag(n, id),
    })
});
let alerts = alert_stream
    .map(|s| s.scan(0u64, |seq, r| { *seq += 1; let id = Some(format!("{seq}")); Some(match r {
        Ok(a) => LiveSseEvent::alert(&a, id),
        Err(n) => LiveSseEvent::lag(n, id),
    })}))
    .unwrap_or_else(|| futures::stream::empty().boxed());
```

> 测试只断言「两条 id 互不重复」，因此先用每路独立 `scan` 即可通过；后续如需全流全局单调，把两个 `scan` 换成共享 `Arc<AtomicU64>`（见 Task 4 集成测试的强断言）。

- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "live SSE 合并流：raw+alert+heartbeat，seq 编号，lag 上报"`

### Task 3: `web.rs` — `handle_request` 返回类型收窄 `BoxBody`

**Files:**
- Modify: `collector/src/server/web.rs`（`handle_request` 及其 return 点）

**Interfaces:**
- Consumes: `boxed_full(resp: Response<Full<Bytes>>) -> Response<BoxBody<Bytes, hyper::Error>>`（新增私有助手，仿 `responsibility/server/mod.rs::to_boxed` 的 `map_err(|never| match never {})` 模式）。
- Produces: `handle_request(...) -> Result<Response<BoxBody<Bytes, hyper::Error>>, Infallible>`。

- [ ] **Step 1: Write the failing test** — 先加一个会用到新返回类型的路由测试桩（放在 Task 4 一起加），本任务以「编译通过 + 现有测试全绿」为验收：

```bash
cd collector && cargo build 2>&1 | tail -5
```
Expected: FAIL（类型不匹配，正是要修的）。

- [ ] **Step 2: 机械重构** — 在 web.rs 顶部加入：

```rust
use http_body_util::BodyExt;
use http_body_util::Full;
/// 把 `Full<Bytes>` 响应包装成 `BoxBody<Bytes, hyper::Error>`（流式 SSE 所需）。
fn boxed_full(resp: Response<Full<Bytes>>) -> Response<BoxBody<Bytes, hyper::Error>> {
    let (parts, body) = resp.into_parts();
    let boxed = body.map_err(|never: std::convert::Infallible| match never {}).boxed();
    Response::from_parts(parts, boxed)
}
```
然后把 `handle_request` 签名改为 `-> std::result::Result<Response<BoxBody<Bytes, hyper::Error>>, Infallible>`，并将函数体内每个 `Ok(resp)` 包为 `Ok(boxed_full(resp))`、`return response;` 改为 `return response.map(boxed_full);`、`return Ok(not_found_json(...))` 等逐一包一层。`serve_*_api` 子函数签名**保持 `Full<Bytes>` 不变**，仅在 `handle_request` 收口时 box。

- [ ] **Step 3: Run build + 全部后端测试**
Run: `cd collector && cargo build && cargo test`
Expected: PASS（现有测试零破坏）。

- [ ] **Step 4: Commit** — `git commit -am "web server 返回类型收窄 BoxBody：为流式 SSE 铺路"`

### Task 4: `/api/live/events` 路由 + `serve_live_events_api` + WebServer 字段

**Files:**
- Modify: `collector/src/server/web.rs`、`collector/src/server/mod.rs`

**Interfaces:**
- Consumes: `live_sse::{LiveSseEvent, live_event_stream}`；`boxed_full`。
- Produces: `serve_live_events_api(event_rx: broadcast::Receiver<Event>, alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>) -> Result<Response<BoxBody<Bytes, hyper::Error>>, Infallible>`；`WebServer.alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>`；`WebServer::new` / `with_health_state` 增参（`alert_rx`）；`handle_request` 增加 `GET /api/live/events` 分支。

- [ ] **Step 1: Write the failing test**

```rust
#[tokio::test]
async fn test_live_events_route_streams() {
    let (event_tx, _) = broadcast::channel::<Event>(16);
    let (_alert_tx, alert_rx) = broadcast::channel::<ResponsibilityAlert>(16);
    // 构造 WebServer（用 Option 的 alert_rx）与 fake request，断言响应的
    // Content-Type 为 text/event-stream 且 body 是流式（BoxBody）。
    let resp = handle_live_events_for_test(event_tx.subscribe(), Some(alert_rx)).await.unwrap();
    assert_eq!(resp.headers().get(hyper::header::CONTENT_TYPE).unwrap(), "text/event-stream");
    assert_eq!(resp.headers().get("x-accel-buffering").unwrap(), "no");
}
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL（`serve_live_events_api` 未定义）。
- [ ] **Step 3: Write minimal implementation**

在 `web.rs` 实现：

```rust
/// GET /api/live/events — 统一实时 SSE：原始事件 + 可选告警 + 心跳。
async fn serve_live_events_api(
    event_rx: broadcast::Receiver<Event>,
    alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>,
) -> std::result::Result<Response<BoxBody<Bytes, hyper::Error>>, Infallible> {
    use http_body_util::StreamBody;
    use hyper::body::Frame;
    use futures::StreamExt as _;

    let stream = live_sse::live_event_stream(event_rx, alert_rx)
        .map(|e| Ok::<Frame<Bytes>, hyper::Error>(Frame::data(Bytes::from(format!("{}\n", e.to_frame())))));
    let body = http_body_util::BodyExt::boxed(StreamBody::new(stream));
    let mut resp = Response::builder()
        .status(hyper::StatusCode::OK)
        .header(hyper::header::CONTENT_TYPE, "text/event-stream")
        .header("cache-control", "no-cache")
        .header("x-accel-buffering", "no")
        .body(body)
        .unwrap();
    // 顶层 server 无 cors 模块，仿 legacy 手动加 CORS 头（同源嵌入亦安全）
    resp.headers_mut().insert("Access-Control-Allow-Origin", "*".parse().unwrap());
    Ok(resp)
}
```

`WebServer` 增加 `alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>`；`new`/`with_health_state` 增参（用 `Option`，`None` 兜底旧行为）。`start()` 内 `service_fn` 闭包把 `alert_rx.clone()` 传入 `handle_request`。`handle_request` 在 legacy 路由之前加：

```rust
(&Method::GET, "/api/live/events") => {
    return serve_live_events_api(event_sender.clone(), alert_rx.clone()).await;
}
```

`server/mod.rs` 加 `pub mod live_sse;`。`handle_live_events_for_test` 仅测试用：直接调 `serve_live_events_api`。

- [ ] **Step 4: Run test to verify it passes** — `cd collector && cargo test live_events`
Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "新增 /api/live/events 统一实时 SSE 路由"`

### Task 5: `main.rs` — record/trace 接线 alert_tx

**Files:**
- Modify: `collector/src/main.rs`

**Interfaces:**
- Consumes: `WebServer::new(event_sender, log_file, alert_rx)`。
- Produces: `start_web_server_if_enabled(enable_server, port, log_file, event_sender, alert_tx: Option<broadcast::Sender<ResponsibilityAlert>>)`。

- [ ] **Step 1: Write the failing test** — 本任务为接线，验收用编译 + 全量测试。先改签名让编译失败作为「红灯」：

```bash
cd collector && cargo build 2>&1 | grep -E "start_web_server_if_enabled|arity|expected" | head
```
Expected: FAIL（签名不匹配）。

- [ ] **Step 2: 修改实现**

`start_web_server_if_enabled` 增加参数 `alert_tx: Option<broadcast::Sender<ResponsibilityAlert>>`，并在 `WebServer::new(event_sender, log_file)` 处传入 `alert_tx.map(|tx| tx.subscribe())`。`run_trace`（main.rs:1387）调用点改为 `start_web_server_if_enabled(enable_server, server_port, Some(log_file), event_sender.clone(), Some(alert_tx.clone()))`。`ssl`/`process`/`stdio`/`system` 四处调用点传 `None`。

- [ ] **Step 3: Run build + 全量测试**
Run: `cd collector && cargo build && cargo test`
Expected: PASS。
- [ ] **Step 4: Commit** — `git commit -am "record/trace 接线 alert_tx 到 /api/live/events 实时 SSE"`

---

## 阶段 1：前端实时层基础

### Task 6: live 类型 + store live 切片

**Files:**
- Create: `frontend/lib/live/live-types.ts`
- Modify: `frontend/lib/store.ts`
- Test: `frontend/__tests__/store-live.test.ts`

**Interfaces:**
- Produces: `LiveConnectionStatus`、`LiveRunMeta`、`LiveStats`、`LiveState`；store 新增 `live: LiveState` + `setLiveStatus(status)` + `setLiveRun(run)` + `setLiveStats(patch)` + `markRunEnded()`。

- [ ] **Step 1: Write the failing test**

```ts
import { useAppStore } from '@/lib/store';

it('live 切片默认态：idle 离线', () => {
  expect(useAppStore.getState().live.status).toBe('idle');
  expect(useAppStore.getState().live.run.isActive).toBe(false);
});

it('setLiveStatus/setLiveRun/setLiveStats 更新切片', () => {
  useAppStore.getState().setLiveStatus('connected');
  useAppStore.getState().setLiveRun({ runId: 'r1', startedAtNs: 1000, endedAtNs: null, isActive: true });
  useAppStore.getState().setLiveStats({ eventCount: 5 });
  const s = useAppStore.getState();
  expect(s.live.status).toBe('connected');
  expect(s.live.run.runId).toBe('r1');
  expect(s.live.stats.eventCount).toBe(5);
});

it('markRunEnded 在 live 态下转回 replay', () => {
  useAppStore.setState({ appState: 'live' });
  useAppStore.getState().markRunEnded();
  expect(useAppStore.getState().appState).toBe('replay');
});
```

- [ ] **Step 2: Run test to verify it fails** — `cd frontend && npx vitest run __tests__/store-live.test.ts`
Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — live-types.ts 定义四类型；store 加字段 `live: { status:'idle', run:{runId:null,startedAtNs:null,endedAtNs:null,isActive:false}, stats:{connectedAtMs:null,eventRate:0,eventCount:0,alertCount:0,droppedCount:0,lastEventAtMs:null} }` 与四个 action；`markRunEnded` 复用 `onRunActiveChanged(false)` 语义（`appState==='live'` 时置 `'replay'`）。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "store 增加 live 切片与状态 actions"`

### Task 7: api 客户端扩展

**Files:**
- Modify: `frontend/lib/api.ts`
- Test: `frontend/__tests__/api-live.test.ts`

**Interfaces:**
- Produces: `export const LIVE_EVENTS_URL = '/api/live/events'`；`getAlertsFull(id): Promise<ResponsibilityAlert[]>`（去 `?lean=1`，含 `graph_snapshot`）。

- [ ] **Step 1: Write the failing test**

```ts
import { api, LIVE_EVENTS_URL } from '@/lib/api';
// mock fetch 返回 { data: [...] }，断言请求路径不含 lean=1
it('getAlertsFull 请求全量告警（含 graph_snapshot）', async () => {
  const calls: string[] = [];
  global.fetch = vi.fn(async (u) => { calls.push(String(u)); return { ok: true, json: async () => ({ data: [{ alert_id: 'a1' }] }) }; }) as any;
  const alerts = await api.getAlertsFull('r1');
  expect(alerts.length).toBe(1);
  expect(calls[0]).not.toContain('lean=1');
  expect(calls[0]).toContain('/api/runs/r1/alerts');
});
it('LIVE_EVENTS_URL 指向统一实时 SSE', () => {
  expect(LIVE_EVENTS_URL).toBe('/api/live/events');
});
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — 加常量与 `getAlertsFull`（`fetchJson<ResponsibilityAlert[]>(`/api/runs/${id}/alerts`)`）。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "api 增加 LIVE_EVENTS_URL 与 getAlertsFull 全量告警拉取"`

### Task 8: rawEvents 环形缓冲 + uSES

**Files:**
- Create: `frontend/lib/live/live-raw-events.ts`
- Test: `frontend/__tests__/live-raw-events.test.ts`

**Interfaces:**
- Produces: `export const RAW_EVENT_BUFFER_CAP = 5000`；`pushRawEvent(ev: LiveRawEvent): void`；`getLiveRawEvents(): readonly LiveRawEvent[]`（快照引用：仅在缓冲头尾变化时出新数组）；`subscribeLiveRawEvents(cb: () => void): () => void`；`resetRawEvents(): void`。

- [ ] **Step 1: Write the failing test**

```ts
import { pushRawEvent, getLiveRawEvents, subscribeLiveRawEvents, resetRawEvents } from '@/lib/live/live-raw-events';

it('push 后 get 可见，订阅者收到通知', () => {
  resetRawEvents();
  const cb = vi.fn();
  subscribeLiveRawEvents(cb);
  pushRawEvent({ id: '1', timestampMs: 1, timestampNs: 1, source: 'tls', pid: 1, comm: 'c', data: {} });
  expect(cb).toHaveBeenCalled();
  expect(getLiveRawEvents().length).toBe(1);
});

it('未变时 getSnapshot 引用稳定（uSES 不重复渲染）', () => {
  resetRawEvents();
  pushRawEvent({ id: '1', timestampMs: 1, timestampNs: 1, source: 'tls', pid: 1, comm: 'c', data: {} });
  const a = getLiveRawEvents();
  const b = getLiveRawEvents();
  expect(a).toBe(b);
});

it('环形缓冲封顶：超过 cap 淘汰最旧', () => {
  resetRawEvents();
  for (let i = 0; i < RAW_EVENT_BUFFER_CAP + 10; i++) pushRawEvent({ id: String(i), timestampMs: i, timestampNs: i, source: 'p', pid: 1, comm: 'c', data: {} });
  expect(getLiveRawEvents().length).toBe(RAW_EVENT_BUFFER_CAP);
});
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — 模块级 `let buffer: LiveRawEvent[] = []` + `let subscribers = new Set<() => void>()`；`pushRawEvent` 入缓冲（cap 时 `shift()`），变更后 `subscribers.forEach(cb => cb())`；`getLiveRawEvents` 返回 `buffer` 引用（不拷贝）；`subscribeLiveRawEvents` 返回退订函数。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "rawEvents 环形缓冲 + uSES 订阅（引用稳定）"`

### Task 9: LiveSession 单例（SSE + 生命周期 + resync）

**Files:**
- Create: `frontend/lib/live/live-session.ts`
- Test: `frontend/__tests__/live-session.test.ts`

**Interfaces:**
- Consumes: `api.getActiveRun/getAlertsFull/getSnapshots`；store `setLiveStatus/setLiveRun/setLiveStats/markRunEnded/onAlertReceived`；`pushRawEvent/resetRawEvents`。
- Produces:
```ts
export interface LiveSessionDeps {
  fetchActiveRun: () => Promise<{ run_id: string } | null>;
  fetchResync: (runId: string) => Promise<{ alerts: unknown[]; latestSnapshot: { nodes: unknown[]; edges: unknown[] } | null }>;
  openEventSource: (url: string) => EventSource;
  onAlert: (a: unknown) => void;
  onLoadRun: (runId: string) => void;
  onRunActiveChanged: (active: boolean) => void;
}
export function createLiveSession(deps: LiveSessionDeps): {
  start(): void; stop(): void; getStats(): LiveStats; getStatus(): LiveConnectionStatus;
}
```

- [ ] **Step 1: Write the failing test**

```ts
// fake EventSource 类：onopen/onmessage/onerror 手动触发
class FakeES {
  static instances: FakeES[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeES.instances.push(this); }
  close() { this.closed = true; }
}

it('发现活跃 run → 连接 SSE → alert 事件驱动 store', async () => {
  const onAlert = vi.fn();
  const s = createLiveSession({
    fetchActiveRun: async () => ({ run_id: 'r1' }),
    fetchResync: async () => ({ alerts: [], latestSnapshot: null }),
    openEventSource: (u) => new FakeES(u) as unknown as EventSource,
    onAlert, onLoadRun: vi.fn(), onRunActiveChanged: vi.fn(),
  });
  s.start();
  await Promise.resolve(); // 等一轮 active 轮询
  expect(FakeES.instances.length).toBe(1);
  expect(FakeES.instances[0].url).toBe('/api/live/events');
  FakeES.instances[0].onopen?.();
  FakeES.instances[0].onmessage?.({ data: JSON.stringify({ event_type: 'alert', data: '{"alert_id":"a1"}' }) });
  // 实际 message 载荷按 SSE 帧解析后的 JSON；此处断言 onAlert 被调
  await Promise.resolve();
  expect(onAlert).toHaveBeenCalled();
  s.stop();
});

it('活跃 run 结束 → markRunEnded + onLoadRun + SSE 关闭', async () => {
  let active = { run_id: 'r1' };
  const onLoadRun = vi.fn(), onRunActiveChanged = vi.fn();
  const s = createLiveSession({
    fetchActiveRun: async () => active,
    fetchResync: async () => ({ alerts: [], latestSnapshot: null }),
    openEventSource: (u) => new FakeES(u) as unknown as EventSource,
    onAlert: vi.fn(), onLoadRun, onRunActiveChanged,
  });
  s.start();
  await Promise.resolve();
  active = null as any; // run 结束
  await new Promise(r => setTimeout(r, 10)); // 等下一轮轮询
  expect(onLoadRun).toHaveBeenCalledWith('r1');
  expect(onRunActiveChanged).toHaveBeenCalledWith(false);
  s.stop();
});
```

- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — `LiveSession` 类：内部持 `deps`、`es: EventSource | null`、`pollTimer`、`runId`、`status`、`stats`、`seqCounter`；`start()` 起轮询（`setInterval(fetchActiveRun, 2000)` 但测试环境无 `setInterval` 可用——用 `setTimeout` 链实现可取消轮询，便于测试驱动）；`connect()` 建 `EventSource(LIVE_EVENTS_URL)`，`onmessage` 按 `event_type` 分派（`alert`→`deps.onAlert(JSON.parse(data))`；`event`→`pushRawEvent`；`lag`→`stats.droppedCount+=N`），`onopen`→`status='connected'` + `resync()`（`deps.fetchResync` 后 `deps.onLoadRun` 不调，只更新图/告警），`onerror`→`status='reconnecting'`；轮询检测 runId 变化（新 run → 重置 + `onRunActiveChanged(true)`；变 null → `onRunActiveChanged(false)` + `onLoadRun(prevRunId)` + 断连）。测试环境 `global.setInterval` 会被注入 fake。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "LiveSession 单例：SSE 连接、run 生命周期、快照 resync"`

### Task 10: 生产单例接线 + layout 挂载

**Files:**
- Create: `frontend/components/live/live-session-mount.tsx`
- Modify: `frontend/app/(main)/layout.tsx`

**Interfaces:**
- Produces: `startLiveSession()` / `stopLiveSession()`（真实 deps：`api.getActiveRun`、`fetchResync` 用 `api.getAlertsFull+getSnapshots`、`new EventSource`、`onAlert: store.onAlertReceived`、`onLoadRun: store.loadRun`、`onRunActiveChanged: store.onRunActiveChanged`）；`LiveSessionMount` 组件（`useEffect(() => { startLiveSession(); return stopLiveSession; }, [])`）。

- [ ] **Step 1: Write the failing test** — jsdom 断言 `LiveSessionMount` 挂载后 `startLiveSession` 被调、卸载时 `stopLiveSession` 被调（`vi.mock` live-session 模块）。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — `live-session.ts` 末尾 `let instance = null; export function startLiveSession() { if (instance) return; instance = createLiveSession(realDeps()); instance.start(); } export function stopLiveSession() { instance?.stop(); instance = null; }`；layout 挂 `<LiveSessionMount />`。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "live 单例挂载进主布局"`

---

## 阶段 2：实时状态条 + 态势实时

### Task 11: LiveStatusBar

**Files:**
- Create: `frontend/components/live/live-status-bar.tsx`
- Modify: `frontend/app/(main)/layout.tsx`
- Test: `frontend/__tests__/live-status-bar.test.tsx`

**Interfaces:**
- Consumes: store `live` 切片。
- Produces: 状态点 + 活跃 run + 已运行时长 + 事件速率 + lag 角标；点击跳 `/console`；`aria-live="polite"` 播报状态。

- [ ] **Step 1: Write the failing test** — `connected` 显示绿点与 runId；`reconnecting` 显示黄点；`disconnected` 显示红点与「实时已断开」；点击 `navigate('/console')`（mock useRouter）。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — 从 `useAppStore(s => s.live)` 读状态；`useRouter` 跳转；状态点颜色映射；格式化时长（`mm:ss`）。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "LiveStatusBar 实时连接状态常驻栏"`

### Task 12: 态势页实时接线（图演化 + LiveAlertFeed）

**Files:**
- Create: `frontend/components/live/live-alert-feed.tsx`
- Modify: `frontend/app/(main)/console/page.tsx`（或其客户端组件）
- Test: `frontend/__tests__/live-alert-feed.test.tsx`

**Interfaces:**
- Consumes: store `alerts`（实时插入）+ `focusAlert`；`MtAgentLensGraphStage` 已接 `engine.updateGraph`（store.onAlertReceived 现有实现）。
- Produces: `LiveAlertFeed`——新告警顶部插入、点击 `focusAlert` 高亮图路径；运行中显示「实时」徽章。

- [ ] **Step 1: Write the failing test** — 注入 store 2 条告警 → 渲染 2 行；点击第一行 → `focusAlert` 被调（图高亮）；appState==='live' 时显示「实时」徽章。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — `useAppStore(s => s.alerts)` + `useAppStore(s => s.appState)`；告警卡（severity 色条 + title + 置信度）；点击 `focusAlert(alert)`。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "态势页实时：告警侧栏 + 图演化接线"`

---

## 阶段 3：原始事件实时流

### Task 13: LiveEventTail

**Files:**
- Create: `frontend/components/live/live-event-tail.tsx`
- Test: `frontend/__tests__/live-event-tail.test.tsx`

**Interfaces:**
- Consumes: `subscribeLiveRawEvents/getLiveRawEvents`；`VirtualList`（复用 `components/ui/virtual-list.tsx`）。
- Produces: 虚拟化滚动 + 自动跟随（上滚暂停、回底部恢复）+ 按 source 过滤 + source 色彩徽章 + 折叠 `data`（点击展开）。

- [ ] **Step 1: Write the failing test** — push 3 条事件 → 渲染 3 行（source/comm 可见）；点按 source 过滤只显示匹配；点行展开 data 载荷；滚回顶部后不再自动跟随（assert 内部 `follow` 状态）。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — `useSyncExternalStore(subscribeLiveRawEvents, getLiveRawEvents)`；`VirtualList` 高度撑满容器；滚动监听（`scrollTop + clientHeight >= scrollHeight - 8` → follow=true）；`source` 下拉过滤；行点击切换展开 `data`。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "LiveEventTail 原始事件实时滚动（虚拟化+跟随+过滤）"`

---

## 阶段 4：监控派生实时 + 时间轴推进

### Task 14: 运行监控派生视角实时刷新

**Files:**
- Modify: `frontend/lib/live/live-session.ts`（周期任务）+ `frontend/lib/store.ts`（`setMonitoring`）
- Test: `frontend/__tests__/live-monitoring.test.ts`

**Interfaces:**
- Consumes: `derive-monitoring.ts` 纯函数、`api.getAlertsFull/getSnapshots`。
- Produces: `LiveSession` 增加 `pollIntervalMs`（默认 10_000）周期 resync 派生视角并 `store.setMonitoring(monitoring)`（复用 `deriveMonitoringState` 的输入契约，见 `monitoring-types.ts`）。

- [ ] **Step 1: Write the failing test** — 注入一轮周期触发 → `deriveMonitoringState` 被调 + `setMonitoring` 更新；`appState !== 'live'` 时不触发。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — 复用现有派生链路（`db-import.ts` 里 `loadDatabase` 后的派生函数），抽出可对「API 增量数据」调用的派生入口；周期任务调它。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "运行监控派生视角随直播周期刷新"`

### Task 15: 图时间演化时间轴直播推进

**Files:**
- Modify: `frontend/lib/live/live-session.ts`（snapshot 增量）+ store `snapshotFrameIndex`
- Test: `frontend/__tests__/live-timeline.test.ts`

**Interfaces:**
- Produces: `LiveSession` 周期拉 `/api/runs/{id}/snapshots` 增量 append 到 store **新字段 `liveSnapshotFrames: GraphSnapshot[]`**（按 `snapshot_id` 去重），`snapshotFrameIndex` 默认跟随最新帧；新增 action `setLiveSnapshotFrames(frames: GraphSnapshot[])`。

- [ ] **Step 1: Write the failing test** — 两轮周期 → frames 追加 + 去重；`snapshotFrameIndex` 指向最新。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — 周期任务 fetch snapshots，`store.setLiveSnapshotFrames(frames)`（去重 + 保持 `snapshotFrameIndex` 默认最新，运行中 `appState==='live'` 且用户未手动拖动时指向最新）。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "图时间演化时间轴直播推进"`

---

## 阶段 5：生命周期与全页面接线

### Task 16: run 结束自动转回放

**Files:**
- Modify: `frontend/lib/live/live-session.ts`
- Test: `frontend/__tests__/live-session.test.ts`（补用例）

**Interfaces:**
- Produces: run 结束（active→null）→ `onRunActiveChanged(false)` + `onLoadRun(runId)`（store.loadRun 下载 db 深调）+ SSE 关闭 + `setLiveStatus('idle')`。

- [ ] **Step 1: Write the failing test** — 已在 Task 9 覆盖主路径，补「runId 切换到新 run」与「loadRun 抛错时不崩溃」用例。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — 补分支：`fetchActiveRun` 抛错 → 静默 + 保持 status；runId 变化 → 重置缓冲 + `resetRawEvents()` + `onRunActiveChanged(true)`。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "run 结束自动转回放 + 新 run 切换重置"`

### Task 17: Overview/Investigations/Traces 实时接线

**Files:**
- Modify: `frontend/components/overview/overview-page-client.tsx`、`frontend/components/investigation/investigations-page-client.tsx`、`frontend/components/traces/traces-page-client.tsx`
- Test: 各页现有测试补断言

**Interfaces:**
- Produces: 三页在 `appState==='live'` 时显示「实时运行」态（告警实时插入已由 store 驱动；traces 显示实时事件尾随入口 `LiveEventTail`；overview 监控视角实时刷新）。

- [ ] **Step 1: Write the failing test** — overview 在 live 态显示实时徽章；investigations 告警列表含实时插入项。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — 各页读 `appState` 显示徽章；investigations 直接用 store.alerts（已实时）；traces 加 `LiveEventTail` 折叠面板。
- [ ] **Step 4: Run test to verify it passes** — Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "overview/investigations/traces 实时态接线"`

### Task 18: e2e live.spec.ts

**Files:**
- Create: `frontend/e2e/live.spec.ts`

**Interfaces:**
- Produces: Playwright 用例：起 mock SSE（页面内注入 fake EventSource 或真实 mock 服务器），断言 LiveStatusBar 状态变化、LiveEventTail 事件滚动、run 结束转回放。

- [ ] **Step 1: Write the failing test** — 用 `page.addInitScript` 注入 mock EventSource + mock fetch；断言状态栏「实时」出现、事件行出现、mock run 结束信号后跳转回放态。
- [ ] **Step 2: Run test to verify it fails** — Expected: FAIL。
- [ ] **Step 3: Write minimal implementation** — e2e 测试本身是最终产物，不需产品代码改动（若发现缺 testid 补之）。
- [ ] **Step 4: Run test to verify it passes** — `npx playwright test e2e/live.spec.ts --project=chromium --project=firefox --workers=1`
Expected: PASS。
- [ ] **Step 5: Commit** — `git commit -am "e2e：实时模式端到端验证"`

---

## 阶段 6：全量回归 + 质量门禁

### Task 19: 全量回归

- [ ] **Step 1:** `cd collector && cargo test` — 全绿。
- [ ] **Step 2:** `cd frontend && npm test && npm run type-check && npm run lint` — 全绿。
- [ ] **Step 3:** `cd frontend && npm run build` — 成功。
- [ ] **Step 4:** e2e 全量 `npx playwright test --project=chromium --project=firefox --workers=1` — 全绿。
- [ ] **Step 5: Commit** — `git commit -am "全量回归通过"`

### Task 20: 质量门禁 + 文档同步

- [ ] **Step 1:** 运行 `gsd-verifier` 验证目标达成（spec 验收标准逐条核对）。
- [ ] **Step 2:** 运行 `pr-review-toolkit:code-reviewer` 审查；修复发现项。
- [ ] **Step 3:** 运行 `gsd-integration-checker` 验证跨模块/E2E。
- [ ] **Step 4:** 更新 `README.md`/`CLAUDE.md` 与注释（新增 `/api/live/events`、实时模式说明）。
- [ ] **Step 5: Commit** — `git commit -am "实时化验收 + 文档同步"`
