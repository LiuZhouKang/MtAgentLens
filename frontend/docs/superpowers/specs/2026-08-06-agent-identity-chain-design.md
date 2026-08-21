# 责任链路 Agent 识别系统升级 · 设计文档

> **日期：** 2026-08-06
> **范围：** 共享 Agent 视觉识别系统 + 追踪页责任链路全链路升级
> **目标：** 解决「责任链路所有 agent 统一蓝色、统一 Server 图标、无法区分、看不出重点」的问题，达到业界顶级（Linear/Notion 级）专业化水准。

---

## 1. 背景与问题

当前追踪页（`/traces`）责任链路由 `traces-page-client.tsx` 渲染：

- 责任链路的行（`SpanRow`）已按事件类型（kind）分色（agent=蓝 / llm=紫 / tool=橙 / file=绿 / network=青 / boundary=红 / verdict=黄），但**所有 agent 分组头**都是「同一个蓝色 `Server` 图标 + 一串 agent_id 字符串」，agent 与 agent 之间**零视觉区分**。
- 视觉上琥珀色（`#fbbf24`）占比高（品牌 MtlLogo + 判决/警告/Trigger Rules），整体"糊成一团黄"，扫读性差。
- 数据层已具备但完全未使用的能力：demo 数据中 `agent-security`（security-auditor）、`agent-frontend`（frontend-developer）、`agent-builder` 都携带 `claimed_role`，但没有一行代码用它做视觉区分。

## 2. 设计目标 / 成功标准

1. **每个 agent 可瞬间区分**：独特色（稳定 hash 分配）+ 独特 monogram 首字母徽章，永不撞车。
2. **专业 logo 呈现**：Linear/Notion/Slack 同款「渐变圆 + 首字母」范式，无品牌素材依赖。
3. **突出重点**：Critical/Unsafe 事件显性化（红色光晕/警示条），与普通事件形成强对比。
4. **信息完整**：agent 的 `claimed_role`（角色徽章）、span 计数、证据数、最高严重度在链路中可见。
5. **跨页面一致**：`responsibility-chain-preview.tsx`、`traceback-chain.tsx` 共用同一识别系统。
6. **零后端改动、零全局状态**：全部为纯函数派生，加新 agent 不碰渲染代码。
7. **无障碍达标**：≥4.5:1 文本对比度（暗底验证）、头像 `role="img"` + `aria-label`、不单纯依赖颜色。

## 3. 研究结论（agentsight 借鉴 + 全网顶级标准）

### 3.1 agentsight 可借鉴机制

- **Hash 稳定配色**（`categoricalColor(name)`）：同一实体名永远映射同一颜色，用户形成"颜色记忆"。比"按出现顺序分配"稳定得多。
- **多维视觉标记映射**（`BlockAdapters.tsx`）：每个类型不是单一颜色，而是「渐变背景 + 左侧彩条 + 语义图标 + 标签」的组合。
- 结论：agentsight 未做按 agent 品牌/角色区分、未做因果连线——这正是本方案差异化增强的方向。

### 3.2 全网检索确立的标准

**稳定哈希**

- 必须用显式哈希（FNV-1a / djb2），不能依赖运行时随机哈希。
- 哈希 → **调色板索引**（而非直接哈希→RGB）：产出更专业、可控的颜色。
- 参考：agentsight `colors.ts`、`@marko19907/string-to-color`、NVIDIA HEAVY.AI 跨图颜色一致性。

**暗色主题分类色板**

- 亮色系 Okabe-Ito / Tol Bright / Tableau 10 为蓝本，但暗底需**更亮、更高饱和**（Set1 / Carto Bold 方向）。
- 分类色 ≤10 种为宜。

**WCAG 暗底对比度（关键修正）**

- 亮饱和色在近黑背景上常**达不到 4.5:1**（亮橙色实测仅 2.8:1 即失败）——对比度是亮度比，不是感知强度。
- 修正法：锁色相、提亮度；正文/组件需 4.5:1，大号展示文本 3:1，头像白字需 ≥4.5:1。
- Anthropic 暗底（#141413）参考对：橙 #d97757（4.9:1）、蓝 #6a9bcc（6.2:1）、绿 #788c5d（4.7:1）——即"近黑底 + 中高亮度色"组合才是安全方向。

**头像无障碍（设计系统标准）**

- `role="img"` + `aria-label={name}`；非交互，不做键盘焦点。
- 首字母派生：按词拆分取首字母、大写、最多 2 字符；空值回退占位。
- 邻近已显示完整名称时可视作装饰 `aria-hidden`（本方案头像在 ID 旁，故保留 `aria-label` 提供唯一可读名，不重复朗读）。

**Linear 官方设计系统（本项目已对齐）**

- Canvas 近黑 `#010102`（与本项目 `--ln-bg` 完全一致）；表面梯度 `#0f1011 / #141516 / #18191a / #28282c`。
- **官方头像做法：为用户头像提供"更多样的一组颜色"**——印证 monogram 多色方案。
- 徽章：中性 pill（`#d0d6e0` 文本 + 9999px 圆角 + 1px 细边框）、subtle badge（`rgba(255,255,255,0.05)` 底）。
- 语义色：success `#10b981` / warning `#eab308` / danger `#dc2626`。
- 文本层级：`#f7f8f8` 主 / `#d0d6e0` 次 / `#8a8f98` 弱。
- 交互：仅动画 compositor 属性；2px 焦点环；≤200ms 反馈。

## 4. 架构

### 4.1 共享识别系统（纯函数 + 轻组件，零依赖）

```
frontend/lib/agent-identity.ts          ← 纯函数，无 React，可单测
  AGENT_PALETTE (8 色，暗底验证 ≥7:1)
  AGENT_COLOR_OVERRIDES (已知实体 curated 覆盖：agent-security→玫瑰、agent-frontend→蓝、agent-builder→橙，突出主告警 agent)
  fnv1a(s): number                      ← FNV-1a 32-bit 稳定哈希
  agentColor(agentId): string           ← override 优先，否则 palette[fnv1a(id) % 8]
  agentInitials(agentId): string        ← 去前缀"agent-"（小写）→ 按 -/_/. 拆分 → 取前至多两词首字母大写；全空→"?"
  darkenHex(hex, factor): string        ← 纯函数暗化（factor=0.45，供头像底，白字实测 ≥5.3:1）
  agentGradient(agentId): [string,string] ← [主色, darken(主色,0.45)] 渐变对
  contrastWhiteOn(hex): number          ← 白字相对 hex 的对比度（供测试断言）

frontend/components/ui/agent-avatar.tsx  ← 极小组件
  <AgentAvatar agentId size ring />      → 渐变圆 + 白首字母，role="img" + aria-label
```

**关键约束：**

- 所有函数**纯函数、无副作用、无随机**——同一 agent 跨页面/刷新/渲染顺序恒为同色同字。
- `agentInitials` 与 `agentColor` 各自独立派生，颜色碰撞（哈希取模可能同色）由首字母二次消歧，仍可辨识。
- 不引全局状态、不建 Context——渲染处直接 `agentColor(id)` 派生即可。

### 4.2 数据流（一次构建，全链路消费）

- 扩展 `frontend/lib/trace-workbench.ts`：
  - `TraceRunModel` 增加 `actorRoles: Record<string, string>`。
  - `TraceEventModel` 增加 `actorRole: string | null`。
  - `buildTraceRunModel` 从 `alert.primary_agent` + `alert.involved_agents` 填充（两者均有 `agent_id` + `claimed_role`）。
  - 新增纯函数 `summarizeActorEvents(events: TraceEventModel[]): { spans; evidenceCount; highestSeverity }`。
- 组件侧**全部纯派生**：分组头/事件行/详情取 `agentColor(e.actor)`、`<AgentAvatar agentId={e.actor} />`、`actorRoles[e.actor]`。

### 4.3 追踪页责任链路四个触点

| 触点              | 升级内容                                                                                                                                                                                                          |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 分组头            | `AgentAvatar`（渐变圆+首字母）+ agent 专属色 ID + 角色徽章 pill + span 计数 + 该 agent 最高严重度点 + 左侧专属色边条；替换原统一蓝色 `Server` 图标                                                                |
| 事件行（SpanRow） | 标题前加 **7px agent 专属色圆点**（保留 kind 图标表示事件语义；真实数据单 agent 巨量 span 下重复头像纯属噪音，故用色点而非头像）；选中高亮沿用事件 kind 色；Critical/Unsafe → 时间条加红色光晕 + 行左侧红色警示条 |
| 展开详情          | "主体"行升级为 Agent 身份卡：头像 + ID + 角色徽章 + 聚合（spans / 证据数 / 最高严重度）                                                                                                                           |
| 右侧详情面板      | 顶部加 Agent 身份块（头像 + ID + 角色 + 聚合），一眼锁定"谁、什么角色"                                                                                                                                            |

### 4.4 共享组件统一

- `responsibility-chain-preview.tsx`：agent 节点药丸改用 `AgentAvatar` + `agentColor`。
- `traceback-chain.tsx`：节点面包屑的 agent 项应用 `agentColor`。
- 其余组件不扩散（保持改动聚焦）。

## 5. 视觉设计系统（最终锁定）

### 5.1 Agent 分类色板（暗底验证通过）

| 色   | 值        | 相对 #010102 对比度 |
| ---- | --------- | ------------------- |
| 蓝   | `#7aa2ff` | ≈8.1:1              |
| 橙   | `#ffb454` | ≈11.3:1             |
| 绿   | `#3ddc97` | ≈11.4:1             |
| 玫瑰 | `#ff6b9d` | ≈7.6:1              |
| 天青 | `#56ccf2` | ≈11.0:1             |
| 珊瑚 | `#ff8a5c` | ≈8.7:1              |
| 紫   | `#b48ef5` | ≈7.8:1              |
| 灰   | `#9aa5b1` | ≈8.0:1              |

所有色 ≥7.5:1，远超正文 4.5:1 标准。色板源自 Okabe-Ito / Tol Bright / Tableau 10 蓝本，按暗底提亮。

### 5.2 monogram 头像

- 尺寸：18px（分组头）/ 26px（展开详情）/ 28px（详情面板）。
- 填充：`linear-gradient(135deg, color, darken(color, 0.45))`（暗化底保证白字 ≥4.5:1）。
- 白首字母居中，字重 600，字号 ≈ size×0.44。
- **UUID 感知（真实数据修复，2026-08-06）**：`agentInitials` 对 `isUuidLike(id)` 返回 `null` → 头像渲染 lucide `Bot` 专业图标而非无意义数字（真实后端 agent 是 UUID，monogram 会变成 "84" 之类难看数字）。可读名（agent-security 等）仍用首字母。
- 1px 半透明深色描边（`rgba(0,0,0,0.35)`）保证暗底上有清晰轮廓。
- 未知/空 agent → 灰底 + "?"。

### 5.3 短 ID 展示

- `shortAgentId(id)`：UUID → 前 8 位 + "…"（`86091f07…`）；可读名原样返回。
- 应用于：分组头 agent ID、展开详情/右侧面板身份块、左侧进程树、**左侧 Trace 运行选择列表**（原显示完整告警 UUID，同样缩短）。完整 ID 保留在 `title` 悬浮提示。

### 5.3 角色徽章（Linear pill 范式）

- pill：`border-radius: 9999px`，背景 `rgba(255,255,255,0.05)`，文本 `#d0d6e0`（Linear subtle badge），1px 细边框 `rgba(255,255,255,0.08)`。
- 置灰当 `claimed_role` 缺失。

### 5.4 严重度强调（"重点"显性化）

- Critical/Unsafe 事件：时间条 `box-shadow: 0 0 8px red` 光晕 + 行左侧 2px 红色警示条（`#dc2626`）。
- Warning：琥珀点（`#eab308`）标记，不抢 Critical 的视觉权。
- 分组头显示该 agent 最高严重度点（红/琥珀/绿）。

### 5.5 消解琥珀糊感

- Trigger Rules 琥珀保留（语义色），但 agent 专属色注入后链路整体变为"多色分区、按 agent 可扫读"。

## 6. 无障碍

- `AgentAvatar`：`role="img"` + `aria-label={agentId}`；非交互无 tabindex。
- 不单纯依赖颜色区分 agent：颜色 + 首字母 + 角色徽章 + 文本 ID 多重编码。
- 所有文本色经暗底验证 ≥4.5:1。
- 尊重 `prefers-reduced-motion`（头像无动画，不新增动画）。

## 7. 测试策略（TDD 先行）

| 测试文件                                        | 断言                                                                                                                                                                                                                                                     |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__tests__/agent-identity.test.ts`（新）        | `fnv1a` 确定性；`agentColor` 稳定且 ∈ 色板、demo 三 agent 色互异；`agentInitials`（"agent-security"→"S" / "agent-frontend"→"F" / "agent-builder"→"B" / 空→"?" / 多词→前两词首字母）；`darkenHex` 全部色板项白字对比度 ≥4.5:1；`agentGradient` 返回二元组 |
| `__tests__/agent-avatar.test.tsx`（新）         | 渲染首字母；`aria-label` = agentId；`role="img"`；size 生效；空值回退 "?"                                                                                                                                                                                |
| `__tests__/trace-workbench.test.ts`（扩展）     | `actorRoles` 映射正确（demo alert：security-auditor / frontend-developer）；事件 `actorRole` 填充；`summarizeActorEvents` 聚合正确                                                                                                                       |
| `__tests__/traces-page-client.test.tsx`（扩展） | 分组头渲染角色徽章文本（如 "security-auditor"）与 `AgentAvatar`（getByLabelText）；事件行含迷你头像                                                                                                                                                      |
| `e2e/compatibility-smoke.spec.ts`（扩展）       | /traces 断言角色徽章可见、无水平溢出                                                                                                                                                                                                                     |

回归门禁：`vitest` 全绿 + `tsc --noEmit` + `eslint` + `npm run build` + Playwright e2e（chromium）。

## 8. 明确不做（Out of Scope）

- 不改后端 / 不改数据模型（timeline 无 agent_type，用 `claimed_role` + hash 派生即可）。
- 不扩散到 /overview、/investigations、命令中心、侧边栏 rail（共享模块已就绪，后续可复用）。
- 不引入品牌 logo 素材、不引入第三方 UI/图标库。
- 不新增大规模布局/动画（保持 compositor-only 动画纪律）。

## 9. 风险与缓解

| 风险             | 缓解                                                                                                                                                  |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| 哈希取模致同色   | 首字母 + 角色徽章 + 文本 ID 多重编码消歧；`AGENT_COLOR_OVERRIDES` 对已知 demo agent 赋 curated 色（已验证三 agent 均互异且主安全 agent 用玫瑰红突出） |
| 头像在暗底模糊   | 1px 深色描边 + 暗化底 + 白字，测试断言 ≥4.5:1                                                                                                         |
| 改动破坏现有测试 | 关键中文标签（"责任链路"等）不动；新增断言只增不改；全量回归门禁                                                                                      |
| 虚拟列表行高变化 | 复用已有 `VirtualList` dynamic 模式（行高内容驱动），头像尺寸固定不改变行高                                                                           |
