# Handoff Observability Dashboard

## 项目总览

实现一套 Agent 手递手委托的可视化仪表板，用于监控 LLM Agent 之间的委托树、事件流、扇出比率和工作区边界完整性。

## 已实现组件

### Agent A (前端开发者) — 已完成

| 组件 | 文件 | 状态 |
|------|------|------|
| `HandoffDelegationTree` | `frontend/components/ui/handoff-delegation-tree.tsx` | ✅ 完成 |
| `HandoffDashboardShell` | `frontend/components/ui/handoff-dashboard-shell.tsx` | ✅ 完成 |
| API Contract | `benign-multi-handoff/HANDOFF_API_CONTRACT.md` | ✅ 已交付 |

### Agent B (后端开发者) — ✅ 已完成

已在 `collector/src/responsibility/server/` 中实现了完整的后端 API：

| 端点 | 文件 | 状态 |
|------|------|------|
| `GET /api/handoff/delegation-tree` | `routes/handoff.rs` + `handoff_store.rs` | ✅ |
| `GET /api/handoff/metrics` | `routes/handoff.rs` | ✅ |
| `GET /api/handoff/events?limit=N&since_ms=N&sub_type=` | `routes/handoff.rs` | ✅ |
| `GET /api/handoff/events/:id` | `routes/handoff.rs` | ✅ |
| `GET /api/handoff/dependency-edges` | `routes/handoff.rs` | ✅ |
| `POST /api/handoff/probe` | `routes/handoff.rs` | ✅ |
| SSE `/api/events` 推送 `handoff_observability` 事件 | `mod.rs` → `handle_unified_sse()` | ✅ |

**新增文件：**
- `handoff_store.rs` — 内存数据存储层（`DelegationNode`, `HandoffObsMetrics`, `DependencyEdge` 类型 + `HandoffStoreData` 带线程安全读写）
- `routes/handoff.rs` — 所有 REST 端点处理器

**修改文件：**
- `mod.rs` — `ServerState` 增加 `handoff_store` + `handoff_event_tx`；`serve_request()` 注册 `/api/handoff/*` 路由；新增 `handle_unified_sse()` 合并 alert + handoff 事件流
- `routes/mod.rs` — 添加 `pub mod handoff`
- `sse.rs` — `SseEvent` 增加 `handoff()` / `delegation_tree()` 构造器

**API 响应格式：** 所有端点使用 `ApiResponse<T>` 包裹，timeout=provisional 标记。

**数据源：** `HandoffStoreData` 为空初始状态，支持运行时通过 SSE 或 API 注入事件。`seed_demo_data()` 生成演示委托树（5 节点，深 2 层）。

### Agent C (集成者) — 后续完成

- 集成后端 API 到前端组件 (`HandoffDashboardShell` + `HandoffDelegationTree`)
- E2E 冒烟测试（REST 端点已通过单元测试，33 tests passing）

## 技术栈

- **前端:** Next.js 15, React 18, TypeScript, Tailwind CSS, Zustand
- **后端:** Rust (Hyper 1.0 + SSE)
- **状态管理:** Zustand store (`lib/store.ts`)
- **SSE 连接:** `lib/sse.ts` (`connectSSE`)

## 类型定义

所有类型在 `frontend/lib/` 目录下，以 handoff-* 开头的合约文件中：

- `handoff-observability-contract.ts` — 核心事件类型与组件 Props
- `handoff-contract.ts` — 委托树结构 + 告警事件
- `handoff-chain-contract.ts` — 链深度 / 扇出 / 纠正循环

## 快速验证

```bash
cd frontend && npm run dev
# 访问 http://localhost:7395
# 加载 Demo 数据查看 Handoff 面板
```

```bash
cd frontend && npm run type-check
# 确认类型编译通过
```
