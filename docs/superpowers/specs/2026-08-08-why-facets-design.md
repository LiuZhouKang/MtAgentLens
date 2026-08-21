# M5「为什么」告警分面 + 全局归因 — 设计文档

> 日期：2026-08-08 · 里程碑：M5 · 前置：M0 数据地基 / M1 取证链 / M2 身份血缘 / M3 原始事件浏览器 / M4 图时间演化（全部四门全绿，293 测试）

## 一、背景与目标

responsibility.db 的三张「为什么」表——`context_pollution_events`（上下文污染传播）、`policy_evaluations`（策略判定）、`non_link_diagnostics`（意图未链接成动作的原因）——目前只在 **M1 取证链**里按**单条告警**过滤展示（`forensic-chain.ts` 的 `buildForensicChain` 按 `involved_agents` / `intentIds` 过滤）。它们回答的调查问题是「**这条告警为什么发生**」，但缺少全局杠杆：

- 无法回答「**整个 run 里谁在污染、哪个策略边界失守、哪些意图静默失败**」
- 无法用这些信号**导航**告警队列（「只看与污染相关的告警」）

**目标**：把三张「为什么」表变成 `/investigations` 的**导航维度**——告警队列可按「污染 / 违规 / 失配」**分面过滤**；顶部一个可折叠的**「全局归因」面板**给出三表的全局摘要，供扫描与下钻。全部数据经**纯函数**从导入数据派生，无造数。

## 二、现状边界（不重新发明的东西）

- 单条告警的取证链（引擎判定 / 污染链 / 策略矩阵 / 失配诊断）已由 M1 `buildForensicChain` 提供——**不改动**。
- 三表全行已由 M0 `db-import.ts` 提取进 `dbAssets`（`pollutionEvents` / `policyEvaluations` / `nonLinkDiagnostics`），类型在 `types.ts`——**复用**，不重复提取。
- 告警→意图桥接已由 `lib/investigation.ts` 的 `collectAlertEvidenceRefs` + store `evidence` 提供——**复用**。
- 页面导航遵循「统一 4 页」约束——**不新增页面**，全部落在 `/investigations`。

## 三、研究校准：世界级分面/归因交互的三条原则

依据对顶级安全/可观测性产品与分面过滤 UX 模式的研究（SentinelOne SOC Console 统一告警台、faceted search 最佳实践）：

1. **分面状态进 URL，不是 UI 状态**——刷新不丢、可分享、可深链。本项目已有 `?alertId` hash 路由；分面用独立 search param `?facet=`，两者解耦。
2. **防零结果死路 + 空态恢复路径**——0 命中的分面 chip **置灰不可点**；空态明确说明「该分面无告警命中（被过滤 N 条）」并提供**「清除分面」**一键恢复。
3. **生效过滤器可见可移除 + a11y**——生效分面以可移除 chip 展示；结果数变化用 `aria-live` 播报；chips 为 `aria-pressed` 按钮；键盘可操作、可见焦点。

## 四、分面模型

```ts
export type WhyFacetKind = 'pollution' | 'policy' | 'diagnostic';
export type FacetSelection = 'all' | WhyFacetKind;
```

| 分面 | 命中规则（纯函数） |
|---|---|
| `pollution` 污染 | 告警 `involved_agents`（含 primary）∩（污染链 `source_agent_id` ∪ `propagation_path[]` 途经 agent）≠ ∅ |
| `policy` 违规 | 告警 `involved_agents` ∩（`judgment.result === 'Violation'` 的 `subject_agent_id`）≠ ∅ |
| `diagnostic` 失配 | 告警证据引用（`collectAlertEvidenceRefs` → store `evidence` 桥接出的 `intent_ids`）∩（`non_link_diagnostics.intent_id`）≠ ∅ |

**诚实性**：三者全部可计算、可验证；`involved_agents` 缺失或证据桥接为空 → 该告警不命中对应分面（如实，不猜测）。

## 五、纯函数接口（`lib/why-facets.ts`）

### `buildWhyFacets(input): WhyFacets`

```ts
export interface WhyFacets {
  facetCounts: Record<WhyFacetKind, number>;              // 各分面命中的告警数
  alertFacets: Record<string, WhyFacetKind[]>;            // alert_id → 命中的分面集合（去重）
  pollution: PollutionSummary;
  policy: PolicySummary;
  diagnostic: DiagnosticSummary;
}

export function buildWhyFacets(input: {
  alerts: ResponsibilityAlert[];
  pollutionEvents: ContextPollutionEvent[];
  policyEvaluations: PolicyEvaluation[];
  nonLinkDiagnostics: NonLinkDiagnostic[];
  intents: Intent[];                                       // 仅诊断摘要取 tool_name
  evidence: CorrelationEvidence[];                         // 失配分面桥接
  graph: GraphData;                                        // collectAlertEvidenceRefs 所需
}): WhyFacets;
```

#### `PollutionSummary`
```ts
export interface PollutionSummary {
  total: number;                                           // 链总数
  bySource: Array<{ agentId: string; count: number }>;     // 按 source 计数，降序
  byArtifact: Array<{ artifact: string; count: number }>;  // 按污染物计数，降序
  hubAgents: string[];                                     // 出现于 ≥2 链的 agent（source 或 path 成员），升序
  multiHopCount: number;                                   // propagation_path.length ≥ 2 的链数
  maxHops: number;                                         // 最长 hop 数（无链 → 0）
  avgHops: number;                                         // 平均 hop 数，保留 1 位小数（无链 → 0）
}
```

#### `PolicySummary`
```ts
export interface PolicySummary {
  total: number;                                           // 判定总数
  byJudgment: Array<{ result: string; count: number }>;    // judgment.result（缺省 → 'unknown'），降序
  byBoundary: Array<{ boundary: string; count: number }>;  // 按 boundary 计数，降序
  bySubject: Array<{ subjectAgentId: string; count: number }>;
  violationCount: number;
  violationRate: number;                                   // 0..1，total 为 0 → 0
  violationsByBoundary: Array<{ boundary: string; violationCount: number; total: number }>;
}
```

#### `DiagnosticSummary`
```ts
export interface DiagnosticSummary {
  total: number;                                           // 诊断总数
  byReason: Array<{ reason: string; count: number }>;      // 降序
  byIntentId: Array<{ intentId: string; toolName: string | null; count: number }>; // toolName 查 intents，缺失 → null，降序
  intentsAffected: number;                                 // 涉及的去重 intent_id 数（intent_id 为 null 不计）
}
```

### `filterAlertsByFacet(alerts, alertFacets, facet): ResponsibilityAlert[]`

`'all'` → 原样返回；否则返回 `alertFacets[id]` 含该分面的告警（无映射视为不命中）。

### `EMPTY_WHY` 常量
全零摘要的 `WhyFacets`（`dbAssets` 为 null 时页面直接引用，避免每次重建）。

### 排序与相等性约定
- 所有排行按 `count` **降序**；同 count 按第二键（`agentId` / `artifact` / `reason` / `boundary` / `intentId`）升序——保证测试断言与渲染稳定。
- `hubAgents` 升序。
- 空输入 → 全零/空数组，不抛错。

## 六、URL 分面状态（`lib/investigation-route.ts` 扩展）

```ts
export const FACET_PARAM = 'facet';
export function parseFacetSelection(searchParams: { get(k: string): string | null } | null): FacetSelection;
// 'pollution'|'policy'|'diagnostic' 合法；非法/缺失 → 'all'
export function buildFacetHref(facet: FacetSelection, currentSearch: string, currentHash: string): string;
// 'all' → 移除 facet 参数；否则写入。保留 currentHash（alertId 深链）。
```

## 七、组件结构（`components/investigation/why-attribution.tsx`）

```tsx
export default function WhyAttribution({
  why, facet, onFacetChange, dbPresent,
}: {
  why: WhyFacets;
  facet: FacetSelection;
  onFacetChange: (f: FacetSelection) => void;
  dbPresent: boolean;              // dbAssets != null
})
```

**结构**（沿用现有 `ln-*` 设计语言）：
1. **头部行**：`全局归因` eyebrow + 4 个分面 chip（`全部 N` / `污染 N` / `违规 N` / `失配 N`）+ 折叠/展开箭头。
   - chip：`aria-pressed={active}`；**0 命中的分面 chip 置灰 + `disabled`**（原则 2）；生效分面高亮。
   - 结果数变化用 `aria-live="polite"` 播报「N 条告警」。
2. **折叠体**（默认折叠）：三列网格，各表为空 → 该列显示「无数据」。
   - 污染列：`N 链 · 多跳 M` + 源头排行（top，余数折叠省略）+ 枢纽 chips + 最长/平均 hop。
   - 策略列：判定分布条（Violation 红 / Allow 绿 / 其余灰）+ 违规率 + 边界×违规排行。
   - 失配列：reason 分布条 + 涉及意图列表（intent_id + tool_name，每条带 count）。
3. **生效分面 chip**（facet ≠ 'all' 时）：`分面：污染 ×`，可一键移除（回到全部）。
4. **dbPresent = false** → 折叠态显示一行提示「导入 responsibility.db 后启用全局归因」，chips 全部禁用。

**导出辅助组件**：`export function WhyFacetEmptyState({ facet, filteredCount, onClear })`——「该分面无告警命中（已过滤 N 条）」+「清除分面」按钮（原则 2）。

## 八、页面接线（`investigations-page-client.tsx`）

- 新增 `useRouter`；`facet = parseFacetSelection(sp)`；**live 模式（dbAssets 为 null）时 `effectiveFacet = 'all'`**——忽略 URL 分面参数（面板提示导入，避免「live + 残留 facet 参数 → 空态」）。
- `why = useMemo(() => dbAssets ? buildWhyFacets({alerts, pollutionEvents: dbAssets.pollutionEvents, policyEvaluations: dbAssets.policyEvaluations, nonLinkDiagnostics: dbAssets.nonLinkDiagnostics, intents, evidence, graph}) : EMPTY_WHY, [dbAssets, alerts, intents, evidence, graph])`。
  - `intents`/`evidence`/`graph` 均为 **store 顶层字段**（`useAppStore(s => s.intents)` 等），非 dbAssets 子字段。
  - **zustand 教训**：依赖选稳定字段（`dbAssets` 整体），不要在 selector 里 `?? []`。
- `filteredAlerts = useMemo(() => filterAlertsByFacet(alerts, why.alertFacets, effectiveFacet), [alerts, why, effectiveFacet])`。
- `setFacet = useCallback((f) => router.replace(buildFacetHref(f, sp.toString(), window.location.hash), { scroll: false }), [sp, router])`。
- `useAlertModels(filteredAlerts, ...)`——分面后选中项从过滤集派生；`selected` 仍按 hash `alertId` 命中，未命中回退 `models[0]`。
- 渲染顺序：`<WhyAttribution …/>` → （`selected` ? workbench : （facet ≠ 'all' ? `<WhyFacetEmptyState …/>` : `<AlertEmptyState …/>`））。
- workbench 的 `availableAlerts` 传入 `filteredAlerts`——**队列即分面结果**。

## 九、诚实性约束（延续项目铁律）

- 所有 KPI / 分布 / 排行 / 链 = 纯函数从导入数据派生；无逐 hop 时间戳 → 不计算时延（`dt_ns` 恒 null，M1 已定）。
- 空表 → 对应列「无数据」；`dbAssets` null → 「导入后启用」；不硬编码演示值。
- 分面命中不足时不猜测——`involved_agents` 为空则污染/违规不命中，证据桥接为空则失配不命中。

## 十、测试矩阵（TDD，先红后绿）

**fixture 真值校准**（`db-sample.sqlite`，`scripts/gen-db-fixture.mjs`）：
- 告警 `alert-1`/`alert-2` 的 `involved_agents` 均为 `{agent-a}`；timeline `evidence_ref` 全 null、图边 `evidence_refs` 空 → 证据桥接为空。
- 污染链：source `agent-a`×2、`agent-b`×1；path 成员 `{a,b,c}`。
- 策略：Violation subject 仅 `{agent-a}`。
- 诊断：`int-1`(nld-1)、`int-2`(nld-2,nld-4)、`int-3`(nld-3)。

**实测预期**：
- `facetCounts` = { pollution: **2**, policy: **2**, diagnostic: **0** }（两告警均涉及 agent-a → 命中污染/违规；无证据桥接 → 失配如实不命中）
- 污染摘要：bySource `[agent-a×2, agent-b×1]`；hubAgents `['agent-a','agent-b']`；multiHopCount 1；maxHops 3；avgHops 1.0
- 策略摘要：byJudgment `[Compliant×4, Allow×1, Violation×1]`（降序、同值按结果升序）；violationCount 1；violationRate 1/6
- 诊断摘要：byReason `[BelowConfidenceCutoff×2, AmbiguousMatch×1, NoArgumentMatch×1]`；byIntentId `[int-2×2, int-1×1, int-3×1]`；intentsAffected 3

`__tests__/why-facets.test.ts`（纯函数，node 环境）：
1. `buildWhyFacets` 分面计数与 fixture 真值一致（pollution 2 / policy 2 / diagnostic 0）
2. `filterAlertsByFacet` 'all' 原样 / pollution / policy 过滤正确
3. 污染摘要：bySource 降序、hubAgents（≥2 链）、multiHopCount、maxHops、avgHops（含无链 → 0）
4. 策略摘要：byJudgment（含 unknown 归组）、violationCount、violationRate、violationsByBoundary
5. 诊断摘要：byReason 降序、byIntentId 带 tool_name（intents 查无 → null）、intentsAffected（intent_id null 不计）
6. **失配桥接**：构造带 `timeline.steps[].evidence_ref` 的告警 + evidence `ev→int` + 诊断命中 → diagnostic 计数 ≥1（覆盖 fixture 桥接为空的反例）
7. 空输入 → 全零不抛错；无 involved_agents → 不命中污染/违规
8. `parseFacetSelection` 合法/非法/缺失 → 'all'
9. `buildFacetHref` 写入 / 移除（'all'）/ 保留 hash
10. `filterAlertsByFacet` 无映射告警（不在 alertFacets 中）→ 不命中

`__tests__/why-attribution.test.tsx`（jsdom）：
9. 0 命中分面 chip 禁用；生效 chip `aria-pressed`
10. `WhyFacetEmptyState` 显示被过滤数与清除按钮

## 十一、验收标准（四门禁 + 手动）

1. `cd frontend && npm run type-check` ✅
2. `npm run lint` 0 警告 ✅
3. `npx vitest run` 全绿（新增 ≥ 10 用例，总量 ≥ 303）✅
4. `npm run build` ✅
5. 手动：导入 `db-sample.sqlite` → /investigations 顶部可见归因面板；切分面 → 队列收缩、URL 携带 `?facet=`、刷新保持；空分面 → 空态 + 清除；展开面板 → 三列摘要正确。

## 十二、范围外（YAGNI）

- 不新增独立「为什么」页面/模式切换（已被用户否决）。
- 不做每项归因的 agent 级子过滤（分面粒度 = kind 级）。
- 不改 `collectAlertEvidenceRefs` / `buildForensicChain` / M1 取证链。
- 不引入新依赖（facet 状态用 search param + `useRouter`，与现有 Next 工具一致）。
