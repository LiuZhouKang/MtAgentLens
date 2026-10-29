# Frontend

MtAgentLens 的前端。基于 Next.js 16 + React 19 + TypeScript 5 + Tailwind CSS 4，构建产物为静态导出，通过 `rust-embed` 编译进 `mtagentlens` 二进制（开发时也可独立运行）。

## 开发

以下命令在 `frontend/` 执行，要求 Node.js 22.13.0+。

```bash
npm ci
npm run dev            # Next.js 开发服务器，端口 7395
```

开发服务器监听 `http://localhost:7395`，`/api/*` 请求通过 `next.config.ts` 代理到 `http://localhost:7397/api/*`。请单独启动 Rust 后端，并为其指定 `--server --server-port 7397`；`npm run dev` 只启动前端，不启动后端或采集。

前端通过 `lib/api.ts` 请求 Rust 服务端的 REST/SSE 接口。被嵌入二进制后，这些接口由同一进程提供。

## 构建

```bash
npm run build          # next build + normalize-static-export
```

## 质量门禁

```bash
npm run lint           # ESLint
npm run type-check     # tsc --noEmit
npm run format:check   # Prettier
npm run test           # vitest 单元测试
npm run test:e2e       # Playwright E2E
npm run ci:quality     # 以上全部 + coverage + build + a11y/响应式/交互审计
```

`npm run audit:*` 系列提供按维度的 Playwright 审计（a11y、响应式、交互延迟、兼容性、主题、视觉回归）。

## 页面

| 路由                  | 内容                                               |
| --------------------- | -------------------------------------------------- |
| `/`                   | 初始进入页（Landing）                              |
| `/console`            | 态势：3D responsibility stage + 告警入口           |
| `/overview`           | 仪表盘：全局态势、探针覆盖、责任集群摘要、运行监控 |
| `/investigations`     | 告警调查：claim、evidence、traceback 工作台        |
| `/traces`             | 追踪：trace 工作台与回放时间线                     |
| `/research/fuxibench` | FuxiBench 风险图谱                                 |

## 目录

```
app/          页面与路由
components/   按域分组的组件
  command-center/  运行监控
  graph/           图可视化（Sigma/3D stage）
  investigation/   告警调查
  landing/         初始进入页
  overview/        仪表盘
  system/          运行选择器、数据源
  traces/          追踪工作台
  ui/              shadcn copy-paste 基础组件
lib/          数据层纯函数与客户端
  api.ts            服务端接口
  db-import.ts      SQLite 数据源导入
  dataset-summary.ts 概览派生纯函数
  graph-lod.ts      LOD 降载
  layout-worker-client.ts  Web Worker 图布局
  investigation.ts  调查数据派生
  identity-lineage.ts 身份血缘
__tests__/    vitest 单测
e2e/          Playwright E2E
scripts/      构建与质量报告脚本（静态导出归一化等）
```

## 关键设计

- **数据流**：运行 trace 通过 `/api/live/events` SSE 或 SQLite 数据源进入 store（`lib/store.ts`）。页面展示的数据由 `lib/` 下纯函数派生，不臆造。
- **大数据性能**：图布局在 Web Worker 执行（`lib/layout.worker.ts`，主线程同步回退）；LOD 按 raw/agent/cluster 三级降载；时间线/列表用虚拟化；实时流按 rAF 合并更新。
- **组件风格**：`components/ui/` 走 shadcn copy-paste 风格（无 `components.json`），新 UI 组件从官方 registry 提取源码即可，保持无分号 + `"use client"`。
- **深链**：告警走 hash、分面走 query，如 `/investigations#alertId=xxx`。

## 历史展示数据

`public/data/fuxibench-risk-atlas.json` 是 86 案例的历史快照，非当前 93 场景的实验结果。旧生成入口因原输入已归档而退役；现有快照保留。当前实验参考 [实验结果说明](../compare/results/README.md)。
