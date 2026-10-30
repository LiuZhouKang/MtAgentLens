# 实时态势 /overview 试点重构设计 — Linear × Tremor × Sigma

**日期**: 2026-08-01
**状态**: 已批准（brainstorming 完成）
**范围**: 试点重构 `/overview` 页面 + 搭建全局设计系统层

---

## 一、目标

将实时态势页从现有 observatory/prism 风格升级为 **Linear 专业暗色 + Tremor 数据可视化 + Sigma 2D 大图** 的顶级平台质感。作为试点，验证技术组合后推广到其余 6 页。

## 二、技术栈

| 技术 | 用途 | 来源 |
|------|------|------|
| **Linear 设计系统** | 全局 token（画布/表面/强调色/排版） | shadcn Linear 设计系统 token |
| **Tremor** | 图表 + KPI 卡 + 数据条 | @tremor/react |
| **Sigma.js** | WebGL 2D 责任网络大图 | @sigma/core + graphology |
| **AdminLTE 4** | 布局信息架构参考（顶栏/内容区） | 借鉴架构，非复制样式 |
| 现有侧边栏 | 保留已完成的缩放风格 | 本项目 |

## 三、设计系统层（一次性搭建，7 页复用）

### Linear tokens（写入 globals.css）

| Token | 值 |
|-------|-----|
| 画布背景 | `#010102` |
| 表面阶梯 | `#0f1011` → `#191a1b`（4 级）|
| 发丝边框 | `rgba(255,255,255,0.08)` |
| 强调色 | `#5e6ad2`（薰衣草紫，唯一强调）|
| 文本 | 白 `#fafafa` / 次级 `#8a8a8e` / 弱 `#5c5c5f` |
| 字体 | 系统 + JetBrains Mono（数据）|

### 通用组件（`frontend/components/ui/`）

- `metric-card.tsx` — Tremor KPI 卡（标题/数值/趋势/Sparkline）
- `trend-chart.tsx` — Tremor 图表封装（Area/Line/Bar/Donut）
- `status-badge.tsx` — 状态徽章（严重/警告/安全）
- `section-panel.tsx` — Linear 表面面板容器
- `sigma-network.tsx` — Sigma.js 2D 责任网络封装（nodes/edges → 渲染）

## 四、/overview 新版模块

| 区块 | 技术 | 数据 |
|------|------|------|
| 顶栏 | Linear 风格 + AdminLTE 信息架构 | 导航 |
| KPI 行 | Tremor MetricCard + Sparkline | store 聚合 |
| 告警趋势 | Tremor AreaChart | 时间序列 |
| 集群分布 | Tremor DonutChart | cluster models |
| **责任网络舞台** | **Sigma.js 2D** | graph |
| 边界趋势 | Tremor BarChart | cluster models |
| 证据覆盖 | Tremor 进度条 | evidence |

### Sigma.js 网络舞台
- 替换现有 `runtime-stage-cosmos` CSS 分层
- graphology 构建图 → sigma 渲染
- 节点按类型着色（Linear 调色板）
- 支持 hover 高亮、点击选中（复用现有选中逻辑）
- 163 节点/430 边真实规模无压力（WebGL）

## 五、数据接入

- **当前**：`useAppStore` + 类型化数据（已有完整类型层）
- **真实规模**：接入 `compare/results/paper/main_evaluation.json`（86 场景）做评估基准区块
- **未来**：后端 `/api/` REST 端点保留接入层

## 六、验收标准

- [ ] TypeScript 零错误
- [ ] ESLint 零新增警告
- [ ] `npm run build` 通过
- [ ] Playwright 实测：KPI 渲染、图表数据正确、Sigma 图可交互
- [ ] 3D 首页不受影响（home-engine-stage 零改动）

## 七、实施顺序

1. 安装 `@tremor/react` + `@sigma/core` + `graphology`
2. 写 Linear tokens 到 globals.css
3. 建通用组件（metric-card/trend-chart/status-badge/section-panel/sigma-network）
4. 重构 overview：顶栏 → KPI → 趋势 → 集群 → Sigma 舞台 → 边界/证据
5. Playwright 浏览器验证迭代
6. 推广到其余 6 页（后续轮次）

## 八、明确不做

- 首页 3D（react-force-graph-3d）本轮不改，仅未来做 Linear 外观适配
- 后端 / API 不改
- 其余 6 页本轮不动
