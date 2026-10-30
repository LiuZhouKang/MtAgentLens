# MtAgentLens 前端实时化设计规格

日期：2026-08-10
状态：已定稿（用户全权授权，方案 A：统一实时会话 + Store 承载）

## 1. 背景与目标

当前前端是「导入 / 回放」导向：直播（`record`/`trace`）中后端在实时广播告警（`alert_tx`）与发送原始事件（`event_sender`），但两者均无 HTTP 出口；前端只能等 run 结束后下载 db 回放。本规格把前端升级为**实时观测台**，让整个前端所有页面/组件/模块都具备实时服务后端的能力。

### 目标能力（用户已确认）

1. 态势图实时演化 + 告警流实时弹出（核心）
2. 原始事件流实时滚动（live tail）
3. 运行监控派生视角实时刷新（overview 6 视角）
4. 图时间演化时间轴直播推进
5. 运行生命周期自动跟随，运行结束自动转回放深调
6. 离线（无活跃 run）时保持现有导入/样例/回放能力不变

### 传输决策（用户已确认）

- **SSE**（非 WebSocket）：单向推送、浏览器自动重连 + `Last-Event-ID`、同源嵌入式天然可用；Rust 端 SSE 基建（`SseEvent`/`alert_event_stream`/`handle_unified_sse`）已存在且测试完备。
- **原始事件也走 SSE**：与告警同一统一流（Grafana 单连接多路复用范式）。

## 2. 后端现状事实（已核实）

- CLI（`ssl`/`process`/`stdio`/`trace`/`system`）唯一启动的服务器是旧 `WebServer`（`collector/src/server/web.rs`），经 `start_web_server_if_enabled`（main.rs:1524）。
- `record`/`trace` 流程创建 `alert_tx: broadcast::Sender<ResponsibilityAlert>`（main.rs:1254）传给 `ResponsibilityRuntime`；消费循环把最终事件发往 `event_sender: broadcast::Sender<Event>`（main.rs:1405）。
- `responsibility/server`（ServerState + `serve_request`）实现了权威 `/api/events` SSE（alert+handoff）与 `/api/runs/*` REST，但**生产零调用**（仅测试使用）。
- 旧 `web.rs` 的 `/api/events` 是 legacy 日志回放（`_event_sender` 被忽略），前端从未使用。
- 直播数据增量落盘：`trace.log`（JSONL）+ `{log}.responsibility.db`（SQLite）+ snapshot 帧；REST `/api/runs/{id}/alerts|events|snapshots|db|evaluation` 与 `/api/runs/active` 文件/DB 可读。
- `Event` 结构（events.rs:11）`{timestamp, source, pid, comm, data}`，`to_json_value()` 产出 `{timestamp(ms), timestamp_ns, source, pid, comm, data}`——可直接作为 SSE 载荷。

### 核心结论

直播中后端在广播告警、在落盘，前端却只能等 run 结束下载 db。中间缺一层「实时连接」。最小且安全的补法：**扩展现有 WebServer**，新增一条统一实时 SSE 路由，把 `alert_tx` + `event_sender` 暴露给前端。`serve_request` 服务器保持现状不动。

## 3. 后端改动（最小面，TDD）

### 3.1 统一实时 SSE 路由 `/api/live/events`

新增 `collector/src/server/live_sse.rs`：

- `LiveSseEvent { event_type: String, data: String, id: Option<String> }`，帧格式复用现有 `SseEvent::to_frame()` 语义（`id:`/`event:`/`data:` 行，双换行分隔）。
- `live_event_stream(alert_rx: Option<Receiver<ResponsibilityAlert>>, event_rx: Receiver<Event>) -> impl Stream<Item = LiveSseEvent>`：
  - 共享单调 seq（跨类型一条流内递增），每个事件带 `id:`。
  - `alert_rx` 有值：`event: alert`，data = `ResponsibilityAlert` JSON。
  - `event_rx`：`event: event`，data = `Event::to_json_value()` JSON。
  - 任一流 lag 时：`event: lag`，data = `{"dropped":N}`。
  - 心跳注释行 `:ping`（30s 间隔，`StreamExt::interval` 合并）防代理超时。
- `serve_live_events_api(event_rx, alert_rx) -> Response`：`text/event-stream` + `cache-control: no-cache` + `x-accel-buffering: no`（关闭代理缓冲，保实时）。

### 3.2 WebServer 扩展（`server/web.rs`）

- `WebServer` 增加字段 `alert_rx: Option<broadcast::Receiver<ResponsibilityAlert>>`。
- `new()` / `with_health_state()` 签名追加该参数（默认 `None`）。
- `handle_request` 增加 `GET /api/live/events` 分支 → `serve_live_events_api(event_sender, alert_rx)`。legacy `/api/events` **完全不动**（现有测试零破坏）。

### 3.3 record/trace 接线（`main.rs`）

- `start_web_server_if_enabled` 增加参数 `alert_tx: Option<broadcast::Sender<ResponsibilityAlert>>`（调用点订阅成 receiver 传入 WebServer）。
- `run_trace` 传 `Some(alert_tx.clone())`；`ssl`/`process`/`stdio`/`system` 传 `None`（无 runtime 告警；其 `event_sender` 仍可推原始事件，实时 SSE 同样生效——统一行为）。

### 3.4 后端测试

- `live_sse.rs` 单测：alert 帧格式、raw event 帧格式（含 `timestamp_ns`）、lag 帧、seq 单调递增、心跳注释。
- `serve_live_events_api` 集成：注入 fake channel，断言流式响应 Content-Type 与首帧。
- `handle_request` 路由测试：`/api/live/events` 路由存在且与 legacy `/api/events` 并存。

## 4. 前端实时层（主要工作量）

### 4.1 关键设计决策：高频数据不进 zustand

- **低频/结构性数据**（`appState`、`activeRunId`、`alerts`、`graph`、`snapshotFrames`、live run 元信息）→ 走现有 zustand `useAppStore` 扩展切片（保持全局单一数据源、引擎直接从 store 读图）。
- **高频数据**（原始事件流、事件速率统计）→ 存 `LiveSession` 内部环形缓冲，组件经 `useSyncExternalStore` 细粒度订阅，**绝不入 zustand**。
  - 依据：zustand selector 返回新引用会致无限重渲染（项目 memory：zustand-selector-stable-reference）；uSES 外部 store 是高频实时数据在 React 19 下的推荐形态（调研结论）。
  - 渲染批量：rAF 合帧提交（与 store 现有 `alertBuffer`/`scheduleAlertFlush` 同构）；`startTransition` 承载重计算（图布局/聚合）。

### 4.2 模块划分

#### `lib/live/live-types.ts`
```ts
export type LiveConnectionStatus = 'idle'|'connecting'|'connected'|'reconnecting'|'disconnected';
export interface LiveRunMeta { runId: string|null; startedAtNs: number|null; endedAtNs: number|null; isActive: boolean; }
export interface LiveStats { connectedAtMs: number|null; eventRate: number; eventCount: number; alertCount: number; droppedCount: number; lastEventAtMs: number|null; }
export interface LiveRawEvent { id: string; timestampMs: number; timestampNs: number; source: string; pid: number; comm: string; data: unknown; }
```

#### `lib/live/live-session.ts`（核心单例）
- `class LiveSession`：
  - `start()`：启动 `/api/runs/active` 轮询（2s）。发现活跃 run → `connect()`；runId 变化 → 重置实时态（清缓冲、`appState='live'`）；active → null → `appState='replay'` + `loadRun(runId)` 深调 + 断开 SSE。
  - `connect()`：`new EventSource('/api/live/events')`；分派：
    - `alert` → `useAppStore.getState().onAlertReceived(alert)`（复用：rAF 合帧 + `graph_snapshot` → `engine.updateGraph` 即时点亮）。
    - `event` → rawEvent 环形缓冲（cap 5000）+ 更新 stats。
    - `lag` → `droppedCount += N`。
  - 重连/resync：EventSource 原生自动重连；`onopen` → 拉 `/api/runs/{id}/snapshots`（取**最新帧**作全量图，`engine.updateGraph` + store.graph）与 `/api/runs/{id}/alerts`（**全量**，含 `graph_snapshot`，按 `alert_id` 去重后补齐告警列表）。重连多次失败 → `status='disconnected'` + 降级提示，不阻塞页面。
  - `stop()`：关 EventSource + 清轮询。
  - 后台节能：`document.visibilityState === 'hidden'` 时暂停事件速率统计与轮询（恢复时 resync）。
- 导出 `startLiveSession()` / `stopLiveSession()`，由 `app/(main)/layout.tsx` 的 effect 全局常驻挂载。

#### `lib/live/live-raw-events.ts`
- LiveSession 内环形缓冲的外部订阅：`subscribeLiveRawEvents(cb)` / `getLiveRawEvents()`（uSES `getSnapshot` 返回稳定引用：仅当 buffer 头/尾变化时出新数组，否则复用同一引用）。

#### store 扩展（`lib/store.ts`）
- 新增切片字段：`live: { status: LiveConnectionStatus; run: LiveRunMeta; stats: LiveStats }`。
- 新增 actions：`setLiveStatus`、`setLiveRun`、`setLiveStats`、`markRunEnded`。
- 激活既有 `onAlertReceived` / `onRunActiveChanged`（前者已被 LiveSession 调用；后者由 run 生命周期驱动）。

### 4.3 组件

1. **`components/live/live-status-bar.tsx`**（挂 `app/(main)/layout.tsx`，常驻）
   - 连接状态点（connected 绿 / reconnecting 黄 / disconnected 红 / idle 灰）+ 活跃 run + 已运行时长 + 事件速率 + lag 角标。
   - 点击跳 `/console`。`aria-live` 播报连接状态变化（a11y）。
2. **态势实时（console 页）**：复用 `MtAgentLensGraphStage`——引擎已接 `engine.updateGraph`（store.onAlertReceived 已实现）。新增实时告警侧栏 `LiveAlertFeed`（新告警顶部插入、可点击 focusAlert 高亮图路径）。
3. **`components/live/live-event-tail.tsx`**（console 页 / traces 页）
   - `useSyncExternalStore` 订阅 rawEvents 环形缓冲。
   - 虚拟化列表（复用现有 `VirtualList`）+ **自动跟随**（上滚暂停、滚回底部恢复，live tail 通用交互）+ 按 source 过滤 + source 色彩徽章。
   - 默认折叠 `data` 载荷，点击展开（安全：原始事件含 TLS 明文）。
4. **图时间演化时间轴直播推进**：snapshotFrames 由 resync 快照 + 周期性增量拉取（`/api/runs/{id}/snapshots`，运行中 ~10s 一次）实时 append；时间轴默认跟随最新帧，可暂停回看历史帧（`snapshotFrameIndex` 复用现有）。
5. **运行监控派生视角实时刷新**：`derive-monitoring.ts` 纯函数已存在；直播中周期（~10s）从 live API 增量拉取（alerts 计数 / snapshots 帧 / evidence 计数）轻量重派生 6 视角（拓扑漂移随帧推进）。
6. **Overview / Investigations / Traces 实时**：
   - 告警列表实时插入（`store.alerts` 已驱动）。
   - 运行中点击告警可查当前图/证据；运行结束自动转回放提供全量证据（`loadRun`）。
   - traces 运行中显示「实时运行」态（增量行），结束后转完整回放。

### 4.4 前端测试（TDD，红绿循环）

- `live-session.test.ts`（注入 fake EventSource + fetch mock）：
  - 活跃 run 发现 → 连接；`alert` 事件 → `onAlertReceived` 调用 + store.graph 更新。
  - raw `event` → 环形缓冲 + stats。
  - `onopen` → resync 快照拉取 + 去重。
  - run 结束 → live→replay + `loadRun` + SSE 关闭。
  - `lag` → droppedCount。
  - 断线多次失败 → disconnected + 不崩溃。
- `live-raw-events` uSES hook 测试（稳定引用、订阅/退订、rAF 批量）。
- `live-status-bar` / `live-event-tail` 组件测试（jsdom + VirtualList mock）。
- store live slice 测试。
- e2e `e2e/live.spec.ts`：Playwright 起 mock SSE 服务器（或复用 store dev test hook），断言状态栏、事件滚动、run 结束转回放。

### 4.5 兼容性 / 降级

- 后端无 `/api/live/events`（旧二进制/Python 数据服务器）→ EventSource onerror → 状态「实时不可用」，回退现有轮询 + 手动 loadRun，导入/回放不受影响。
- 无活跃 run → 状态栏「离线」，各页面保持现有能力。
- dev 环境 `/api/*` 重写到 Python(7397) → 实时模式开发验证用 e2e mock 或临时把 rewrite 指向 Rust 后端；生产（嵌入式同源）天然可用。

## 5. 错误处理

| 场景 | 处理 |
|------|------|
| SSE 断线 | EventSource 自动重连；onerror → `reconnecting`；onopen → resync 快照补漏 |
| 网络不可达（多次失败） | `disconnected` + 降级提示，不阻塞页面与导入/回放 |
| 单条消息解析失败 | 丢弃 + 计数，不崩流 |
| active 轮询 404 | 视为无活跃 run |
| run 目录不可用 | 实时态显示错误，保留回放兜底 |

## 6. 安全

- 原始事件含 TLS 明文（prompt/token），与现有 `/api/runs/{id}/events` 行为一致（本地工具、同源）。前端默认折叠 `data` 载荷，点击展开。
- 不引入新的跨源面（SSE 同源）；`x-accel-buffering: no` 防代理缓冲导致的延迟。

## 7. 验收标准

1. 直播中（`record -c claude`）：态势图实时增长、告警实时弹出、事件流实时滚动、监控视角随帧刷新、时间轴推进。
2. 运行结束：自动转回放，深度调查可用。
3. 断线重连：SSE 断开自动恢复 + 快照补齐。
4. 全量回归：vitest + `cargo test` + `npm run build` + e2e（chromium/firefox `--workers=1`）。
5. 质量门禁：`gsd-verifier` 目标验证 + `pr-review-toolkit:code-reviewer` 审查 + `gsd-integration-checker` 跨模块验证。

## 8. 实施阶段（writing-plans 拆原子任务）

- **阶段 0**：后端实时 SSE（`live_sse.rs` + `web.rs` + `main.rs` + 后端测试）→ `cargo test` 全绿。
- **阶段 1**：前端 live 类型 + LiveSession + live-raw-events uSES + store live slice + 单测。
- **阶段 2**：LiveStatusBar + 态势实时（图演化 + LiveAlertFeed）。
- **阶段 3**：LiveEventTail（原始事件流实时滚动）。
- **阶段 4**：监控派生实时刷新 + 图时间演化时间轴推进。
- **阶段 5**：run 生命周期自动转换（live→replay）+ Overview/Investigations/Traces 实时接线 + e2e。
- **阶段 6**：全量回归 + code-review + verifier + integration-checker + README/CLAUDE.md 文档同步。
