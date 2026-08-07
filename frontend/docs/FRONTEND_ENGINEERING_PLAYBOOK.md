# MtAgentLens 前端工程卓越手册（Trae 版）

本文档用于把“顶级前端工程范式”落地为本仓库可执行的约束与检查清单，作为后续所有前端开发的统一工作基线。

优先级：如与产品/信息架构约束冲突，以 [PRODUCTION_FRONTEND_DESIGN.md](file:///home/lzk/MtAgentLens/frontend/docs/PRODUCTION_FRONTEND_DESIGN.md) 为准；如与仓库全局开发宪法冲突，以 [CLAUDE.md](file:///home/lzk/MtAgentLens/CLAUDE.md) 与 [AGENTS.md](file:///home/lzk/MtAgentLens/AGENTS.md) 为准。

## 1. 技术栈锁定（不得随意变更）

- 框架：Next.js（App Router）+ React + TypeScript（strict）
- 样式：Tailwind CSS v4（PostCSS 插件）+ HeroUI styles
- 状态/数据：Zustand + TanStack React Query
- Mock：MSW（Vitest/Storybook 共用 handlers）
- 单测：Vitest + Testing Library
- 端到端：Playwright
- 组件工坊：Storybook（组件级开发与文档）

关键配置入口：

- [frontend/package.json](file:///home/lzk/MtAgentLens/frontend/package.json)
- [frontend/next.config.ts](file:///home/lzk/MtAgentLens/frontend/next.config.ts)
- [frontend/tsconfig.json](file:///home/lzk/MtAgentLens/frontend/tsconfig.json)
- [frontend/eslint.config.mjs](file:///home/lzk/MtAgentLens/frontend/eslint.config.mjs)
- [frontend/vitest.config.ts](file:///home/lzk/MtAgentLens/frontend/vitest.config.ts)
- [frontend/playwright.config.ts](file:///home/lzk/MtAgentLens/frontend/playwright.config.ts)
- [frontend/app/globals.css](file:///home/lzk/MtAgentLens/frontend/app/globals.css)

## 2. 交付形态约束：静态导出优先

本仓库的 Next 生产构建启用静态导出（`output: 'export'`），意味着必须把“构建期可计算”当成默认假设，避免引入运行时服务器能力。

落地规则：

- 新增页面前先判断：是否需要运行时 API/Server Actions/依赖请求时状态；若需要，必须先提出明确替代方案（SSE/离线导出/前端请求/构建期生成）。
- 对动态路由：必须提供 `generateStaticParams()` 或改为非动态路由；否则静态导出会失败。
- 对重依赖浏览器 API/3D/大体积可视化：优先 `dynamic(() => import(...), { ssr: false })`，避免 SSR/构建期执行浏览器侧代码。

## 3. 目录与模块边界（可维护性第一）

强制约束：

- 路由层保持薄：`app/**/page.tsx` 只做装配与路由；主要 UI 放入 `components/**`；核心逻辑下沉到 `lib/**`。
- Client/Server 边界清晰：任何使用 hooks、DOM、浏览器 API 的组件必须显式 `'use client'`；能做成 Server Component 的尽量保持为 Server Component。
- “全局能力”集中在 `components/system/`：providers、观测/上报、全局监听、全局上下文。
- 新组件要先选归属域：优先放进现有领域目录（`components/overview|cluster|investigation|traces|command-center|agent|ui|motion*` 等），避免随意新建顶层目录。

推荐实践：

- 复用 `@/` 路径别名导入，避免相对路径爬目录（见 [tsconfig.json](file:///home/lzk/MtAgentLens/frontend/tsconfig.json)）。
- 公共类型与契约放到 `lib/*contract.ts` / `lib/types.ts`，避免类型散落到组件目录导致循环依赖。

## 4. 数据与状态：默认“可复现、可测试”

原则：

- UI 状态与数据状态分离：网络/缓存/异步用 React Query；跨页面可复用 UI 状态用 Zustand；局部 UI 状态尽量用组件内 state。
- 每个请求都要有失败路径：空态、加载态、错误态必须能被单测覆盖。
- 缓存策略显式化：对 “实时监测/告警队列” 类数据，明确刷新策略与上限，避免无边界 refetch。

落地入口：

- React Query 全局默认配置见 [app-providers.tsx](file:///home/lzk/MtAgentLens/frontend/components/system/app-providers.tsx)
- Zustand store 入口见 [store.ts](file:///home/lzk/MtAgentLens/frontend/lib/store.ts)

## 5. 样式与设计：tokens 优先，少写“发明式 CSS”

原则：

- 优先使用现有语义化 CSS 变量与 Tailwind 体系扩展，不引入散乱的颜色/阴影/间距常量。
- 不为单一页面写“只服务当前截图”的样式；任何新增样式要能在 Production Console 与 Presentation Mode 下都不会破坏可读性。
- UI 交互优先可访问性：可聚焦、可键盘操作、视觉对比充分。

落地入口：

- 全局 tokens 与暗色模式变量见 [globals.css](file:///home/lzk/MtAgentLens/frontend/app/globals.css)

## 6. 可观测性与安全：前端也按安全敏感对待

原则：

- 前端上报/日志不得包含 token、secret、cookie、session、凭据、邮箱等敏感信息。
- 复用现有脱敏策略，不另起一套。

落地入口：

- 脱敏与上报策略见 [frontend-observer.tsx](file:///home/lzk/MtAgentLens/frontend/components/system/frontend-observer.tsx)

## 7. 测试与门禁：以仓库现有脚本为“唯一真相”

提交前最小门禁（必须全绿）：

- `npm run format:check`
- `npm run lint`
- `npm run type-check`
- `npm run test`

触发条件门禁（按风险选择，但推荐）：

- 涉及交互/路由/回归风险：补齐 `e2e/` 并运行 `npm run test:e2e`
- 涉及 a11y 风险（新增表单/弹窗/导航/列表交互）：运行 `npm run audit:a11y`（keyboard path + axe scan）
- 涉及视觉回归风险（布局/主题/核心卡片）：运行 `npm run audit:visual`（Playwright screenshot diff）
- 涉及性能/大组件：至少做一次页面级 smoke（首页、核心工作台页）并观察控制台错误与交互卡顿

测试编写约定：

- 单测优先覆盖：空态/加载态/错误态、关键交互（点击/键盘）、关键派生逻辑（filter/sort/group）。
- E2E 优先覆盖：导航主路径、关键 drilldown、回放/筛选/导出等“事故处理闭环”动作。
- 组件开发优先走 Storybook：新增/修改 UI 组件时优先补齐对应 `.stories.tsx`，用 `npm run storybook` 做视觉与交互确认。

## 8. 开发工作流（把“顶级 skill”固化成动作）

建议用下面顺序推进每一个前端任务：

- 先对齐产品原则：对照 [PRODUCTION_FRONTEND_DESIGN.md](file:///home/lzk/MtAgentLens/frontend/docs/PRODUCTION_FRONTEND_DESIGN.md) 确认是否属于 Production Console 或 Presentation Mode，避免混用。
- 明确静态导出约束：确认是否涉及运行时服务端能力；涉及则先做方案评审再动手。
- 先写/改测试（TDD）：让失败用例锁定需求边界，再写最小实现。
- 做性能切片：把“必须交互”的部分做成 Client Component，其余尽量保留 Server；大组件延迟加载。
- 过门禁并收尾：lint/type-check/test → 必要时 e2e → 再做一次界面回归（本地 dev）。
