# Frontend

MtAgentLens 的前端。基于 Next.js 16 + React 19 + TypeScript 5 + Tailwind CSS 4，构建产物为静态导出，通过 `rust-embed` 编译进 `mtagentlens` 二进制（开发时也可独立运行）。

## 开发

```bash
npm install
npm run dev            # Next.js 开发服务器，端口 7395
```

独立运行时，前端通过 `frontend/lib/api.ts` 请求 Rust 服务端的 REST/SSE 接口。被嵌入二进制后，这些接口由同一进程提供。

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
  forensic-chain.ts 取证链
  identity-lineage.ts 身份血缘
__tests__/    vitest 单测
e2e/          Playwright E2E
scripts/      数据生成与构建脚本（fuxibench atlas、静态导出归一化）
```

## 关键设计

- **数据流**：运行 trace 通过 `/api/events` SSE 或 SQLite 数据源进入 store（`lib/store.ts`）。页面展示的数据由 `lib/` 下纯函数派生，不臆造。
- **大数据性能**：图布局在 Web Worker 执行（`lib/layout.worker.ts`，主线程同步回退）；LOD 按 raw/agent/cluster 三级降载；时间线/列表用虚拟化；实时流按 rAF 合并更新。
- **组件风格**：`components/ui/` 走 shadcn copy-paste 风格（无 `components.json`），新 UI 组件从官方 registry 提取源码即可，保持无分号 + `"use client"`。
- **深链**：告警走 hash、分面走 query，如 `/investigations#alertId=xxx`。
