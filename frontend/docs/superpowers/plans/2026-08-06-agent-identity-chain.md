# 责任链路 Agent 识别系统升级 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为追踪页责任链路建立「共享 Agent 识别系统」（稳定 hash 配色 + 专业 monogram 头像 + 角色徽章 + 严重度强调），彻底解决 agent 无法区分、看不清重点的问题，达到 Linear/Notion 级专业化。

**Architecture:** 新建纯函数模块 `lib/agent-identity.ts`（哈希/配色/首字母/暗化/对比度）+ 轻组件 `components/ui/agent-avatar.tsx`；扩展 `lib/trace-workbench.ts` 数据模型携带 `actorRole`/`actorRoles`；追踪页责任链路四触点（分组头/事件行/展开详情/右侧面板）全部消费该识别系统；`responsibility-chain-preview.tsx` 与 `traceback-chain.tsx` 共用同一系统。零后端改动、零全局状态、纯函数派生。

**Tech Stack:** Next.js 16 / React 19 / TypeScript 5 / Tailwind CSS 4 / Vitest / @testing-library / Playwright

## Global Constraints

- 工作目录：`/home/lzk/MtAgentLens/frontend`（所有命令在 frontend 下运行）。
- 所有新函数**纯函数、无副作用、无随机**；`fnv1a` 用显式哈希（禁 `Math.random` / 运行时随机）。
- 色板 `AGENT_PALETTE` 固定为 8 色（见 Task 1）；暗化 factor **0.45**（白字暗底实测 ≥5.3:1）。
- `AgentAvatar` 必须 `role="img"` + `aria-label={agentId}`，非交互（无 tabindex）。
- 现有关键中文锚点（"责任链路"、"进程树"、"过滤"、"全部"、"Tool"、"文件"、"Span 数"、"判决"）**不得改动**。
- 不引第三方图标/UI 库；不新增布局动画（保持 compositor-only 纪律）。
- 每个 Task 结束必须运行对应 vitest 断言全绿。

---

## Task 1: `lib/agent-identity.ts` 纯函数模块 + 单测

**Files:**

- Create: `frontend/lib/agent-identity.ts`
- Test: `frontend/__tests__/agent-identity.test.ts`

**Interfaces:**

- Consumes: 无
- Produces: `AGENT_PALETTE`, `AGENT_COLOR_OVERRIDES`, `fnv1a(s): number`, `agentColor(id): string`, `agentInitials(id): string`, `darkenHex(hex, factor): string`, `agentGradient(id): [string, string]`, `contrastWhiteOn(hex): number`

- [ ] **Step 1: 写失败测试**

创建 `frontend/__tests__/agent-identity.test.ts`：

```ts
import { describe, expect, test } from 'vitest';
import {
  AGENT_COLOR_OVERRIDES,
  AGENT_PALETTE,
  agentColor,
  agentGradient,
  agentInitials,
  contrastWhiteOn,
  darkenHex,
  fnv1a,
} from '@/lib/agent-identity';

describe('agent-identity', () => {
  test('fnv1a is deterministic', () => {
    expect(fnv1a('agent-security')).toBe(fnv1a('agent-security'));
  });

  test('agentColor is stable and drawn from the palette', () => {
    const color = agentColor('agent-frontend');
    expect(AGENT_PALETTE).toContain(color);
    expect(agentColor('agent-frontend')).toBe(color);
  });

  test('known demo agents use curated overrides', () => {
    expect(AGENT_COLOR_OVERRIDES['agent-security']).toBe('#ff6b9d');
    expect(agentColor('agent-security')).toBe('#ff6b9d');
    expect(agentColor('agent-frontend')).toBe('#7aa2ff');
    expect(agentColor('agent-builder')).toBe('#ffb454');
  });

  test('demo agents get mutually distinct colors', () => {
    const colors = new Set(['agent-security', 'agent-frontend', 'agent-builder'].map(agentColor));
    expect(colors.size).toBe(3);
  });

  test('agentInitials strips the agent- prefix', () => {
    expect(agentInitials('agent-security')).toBe('S');
    expect(agentInitials('agent-frontend')).toBe('F');
    expect(agentInitials('agent-builder')).toBe('B');
  });

  test('agentInitials handles multi-word and empty ids', () => {
    expect(agentInitials('agent-data-collector')).toBe('DC');
    expect(agentInitials('unknown')).toBe('U');
    expect(agentInitials('')).toBe('?');
    expect(agentInitials('agent-')).toBe('?');
  });

  test('darkenHex returns a valid hex', () => {
    expect(darkenHex('#7aa2ff', 0.45)).toMatch(/^#[0-9a-f]{6}$/);
  });

  test('white text on darkened avatar fill stays >= 4.5:1 for every palette color', () => {
    for (const hex of AGENT_PALETTE) {
      expect(contrastWhiteOn(darkenHex(hex, 0.45))).toBeGreaterThanOrEqual(4.5);
    }
  });

  test('agentGradient returns a [from, to] pair', () => {
    const [from, to] = agentGradient('agent-security');
    expect(typeof from).toBe('string');
    expect(typeof to).toBe('string');
    expect(to).not.toBe(from);
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/agent-identity.test.ts`
Expected: FAIL，报 `Failed to resolve import "@/lib/agent-identity"`（模块不存在）。

- [ ] **Step 3: 最小实现**

创建 `frontend/lib/agent-identity.ts`：

```ts
/**
 * Agent visual identity — pure functions, no React, no side effects.
 *
 * Every agent_id maps to a stable color + monogram initials, so the same agent
 * is instantly recognizable anywhere in the app. Known demo agents get curated
 * overrides (so the primary security agent is visually prominent); unknown
 * agents fall back to a deterministic hash into a curated dark-theme palette.
 */

export const AGENT_PALETTE = [
  '#7aa2ff', // blue
  '#ffb454', // orange
  '#3ddc97', // green
  '#ff6b9d', // rose
  '#56ccf2', // sky
  '#ff8a5c', // coral
  '#b48ef5', // violet
  '#9aa5b1', // gray
] as const;

/** Curated colors for known agents — wins over the hash fallback. */
export const AGENT_COLOR_OVERRIDES: Record<string, string> = {
  'agent-security': '#ff6b9d', // rose — security incidents are the focal point
  'agent-frontend': '#7aa2ff', // blue — development work
  'agent-builder': '#ffb454', // orange — build/assembly
};

/** FNV-1a 32-bit — explicit stable hash (never the runtime's randomized hash). */
export function fnv1a(input: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

export function agentColor(agentId: string): string {
  const id = agentId || 'unknown';
  return AGENT_COLOR_OVERRIDES[id] ?? AGENT_PALETTE[fnv1a(id) % AGENT_PALETTE.length];
}

/** Monogram initials: strip the "agent-" prefix, then first letter of up to 2 words. */
export function agentInitials(agentId: string): string {
  const id = agentId || '';
  const stripped = id.toLowerCase().startsWith('agent-') ? id.slice('agent-'.length) : id;
  const words = stripped.split(/[-_/.\s]+/).filter(Boolean);
  if (words.length === 0) return '?';
  return words
    .slice(0, 2)
    .map((word) => word[0]!.toUpperCase())
    .join('');
}

/** Darken a #rrggbb hex toward black by factor (0..1). */
export function darkenHex(hex: string, factor: number): string {
  const value = parseInt(hex.slice(1), 16);
  const channel = (shift: number) => Math.round(((value >> shift) & 255) * (1 - factor));
  return `#${[16, 8, 0].map((shift) => channel(shift).toString(16).padStart(2, '0')).join('')}`;
}

/** Avatar gradient fill: [agent color, darkened agent color]. */
export function agentGradient(agentId: string): [string, string] {
  const color = agentColor(agentId);
  return [color, darkenHex(color, 0.45)];
}

/** WCAG contrast ratio of white text on a hex background. */
export function contrastWhiteOn(hex: string): number {
  const relativeLuminance = (input: string): number => {
    const value = parseInt(input.slice(1), 16);
    const [r, g, b] = [(value >> 16) & 255, (value >> 8) & 255, value & 255].map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  return (1.0 + 0.05) / (relativeLuminance(hex) + 0.05);
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/agent-identity.test.ts`
Expected: PASS（8 个用例）。

- [ ] **Step 5: 提交**

```bash
git add frontend/lib/agent-identity.ts frontend/__tests__/agent-identity.test.ts
git commit -m "feat: add agent visual identity pure module (hash color + monogram)"
```

---

## Task 2: `components/ui/agent-avatar.tsx` 组件 + 单测

**Files:**

- Create: `frontend/components/ui/agent-avatar.tsx`
- Test: `frontend/__tests__/agent-avatar.test.tsx`

**Interfaces:**

- Consumes: `agentColor`（Task 1，本组件只用 `agentGradient` 与 `agentInitials`）
- Produces: `export default function AgentAvatar({ agentId, size?, ring? })` — props: `agentId: string`, `size?: number`（默认 18）, `ring?: boolean`（默认 true）

- [ ] **Step 1: 写失败测试**

创建 `frontend/__tests__/agent-avatar.test.tsx`：

```tsx
// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import AgentAvatar from '@/components/ui/agent-avatar';

afterEach(() => cleanup());

describe('AgentAvatar', () => {
  it('renders the monogram initials for an agent id', () => {
    render(<AgentAvatar agentId="agent-security" />);
    expect(screen.getByText('S')).toBeDefined();
  });

  it('is a non-interactive img with the agent id as its accessible name', () => {
    render(<AgentAvatar agentId="agent-security" />);
    const avatar = screen.getByRole('img', { name: 'agent-security' });
    expect(avatar).toBeDefined();
  });

  it('applies the requested size as diameter', () => {
    render(<AgentAvatar agentId="agent-security" size={26} />);
    expect(screen.getByRole('img', { name: 'agent-security' })).toHaveStyle({
      width: '26px',
      height: '26px',
    });
  });

  it('falls back to a question mark for empty ids', () => {
    render(<AgentAvatar agentId="" />);
    expect(screen.getByText('?')).toBeDefined();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/agent-avatar.test.tsx`
Expected: FAIL，报无法解析 `@/components/ui/agent-avatar`。

- [ ] **Step 3: 最小实现**

创建 `frontend/components/ui/agent-avatar.tsx`：

```tsx
'use client';

import { agentGradient, agentInitials } from '@/lib/agent-identity';

type AgentAvatarProps = {
  agentId: string;
  /** Diameter in px. */
  size?: number;
  /** Subtle dark inset ring so the fill reads on near-black surfaces. */
  ring?: boolean;
};

export default function AgentAvatar({ agentId, size = 18, ring = true }: AgentAvatarProps) {
  const [from, to] = agentGradient(agentId);
  const initials = agentInitials(agentId);
  return (
    <span
      role="img"
      aria-label={agentId || 'unknown agent'}
      title={agentId || undefined}
      style={{
        width: size,
        height: size,
        minWidth: size,
        borderRadius: '50%',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: `linear-gradient(135deg, ${from}, ${to})`,
        boxShadow: ring ? 'inset 0 0 0 1px rgba(0,0,0,0.35)' : undefined,
        fontSize: Math.round(size * 0.44),
        fontWeight: 600,
        color: '#fff',
        lineHeight: 1,
        userSelect: 'none',
        flexShrink: 0,
      }}
    >
      {initials}
    </span>
  );
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/agent-avatar.test.tsx`
Expected: PASS（4 个用例）。

- [ ] **Step 5: 提交**

```bash
git add frontend/components/ui/agent-avatar.tsx frontend/__tests__/agent-avatar.test.tsx
git commit -m "feat: add AgentAvatar monogram component with a11y naming"
```

---

## Task 3: `lib/trace-workbench.ts` 携带角色 + 聚合助手 + 单测

**Files:**

- Modify: `frontend/lib/trace-workbench.ts`
- Test: `frontend/__tests__/trace-workbench.test.ts`

**Interfaces:**

- Consumes: `ResponsibilityAlert`（`primary_agent`/`involved_agents` 均含 `{ agent_id, claimed_role: string | null }`）
- Produces: `TraceEventModel.actorRole: string | null`, `TraceRunModel.actorRoles: Record<string, string>`, `ActorSummary = { spans; evidenceCount; highestSeverity }`, `summarizeActorEvents(events: TraceEventModel[]): ActorSummary`

- [ ] **Step 1: 写失败测试（扩展既有测试文件）**

在 `frontend/__tests__/trace-workbench.test.ts` 的 `describe` 内追加两个 `test`：

```ts
test('populates actorRoles and per-event actorRole from the alert agent list', () => {
  const model = buildTraceRunModel(demoAlerts[0], demoEvidence);
  expect(model.actorRoles['agent-security']).toBe('security-auditor');
  expect(model.actorRoles['agent-frontend']).toBe('frontend-developer');
  expect(model.timeline.every((e) => e.actorRole === null || typeof e.actorRole === 'string')).toBe(
    true,
  );
});

test('summarizeActorEvents aggregates spans, evidence count, and highest severity', () => {
  const model = buildTraceRunModel(demoAlerts[0], demoEvidence);
  const firstActor = model.timeline[0].actor;
  const summary = summarizeActorEvents(model.timeline.filter((e) => e.actor === firstActor));
  expect(summary.spans).toBeGreaterThan(0);
  expect(['Normal', 'Info', 'Warning', 'Critical']).toContain(summary.highestSeverity);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/trace-workbench.test.ts`
Expected: FAIL（`actorRoles`/`summarizeActorEvents` 不存在；TS 编译错误同样视为失败）。

- [ ] **Step 3: 实现**

在 `frontend/lib/trace-workbench.ts` 中：

**a)** `TraceEventModel` 增加字段（在 `actor: string;` 之后）：

```ts
actor: string;
actorRole: string | null; // claimed_role of the acting agent, if known
```

**b)** `TraceRunModel` 增加字段（在 `primaryAgent: string;` 之后）：

```ts
primaryAgent: string;
actorRoles: Record<string, string>; // agent_id → claimed_role
```

**c)** `buildTraceRunModel` 内，`const evidenceById = ...` 之后构建角色表：

```ts
const actorRoles: Record<string, string> = {};
for (const entry of [alert.primary_agent, ...alert.involved_agents]) {
  if (entry.claimed_role) actorRoles[entry.agent_id] = entry.claimed_role;
}
```

**d)** timeline 的 `map` 返回对象中，`actor: step.agent_id ?? alert.primary_agent.agent_id,` 之后加：

```ts
      actorRole: step.agent_id ? (actorRoles[step.agent_id] ?? null) : null,
```

**e)** 返回值对象中 `primaryAgent: alert.primary_agent.agent_id,` 之后加：

```ts
    actorRoles,
```

**f)** 文件末尾追加聚合助手：

```ts
export type ActorSummary = {
  spans: number;
  evidenceCount: number;
  highestSeverity: AlertSeverity | 'Normal';
};

const ACTOR_SEVERITY_WEIGHT: Record<AlertSeverity | 'Normal', number> = {
  Normal: 0,
  Info: 1,
  Warning: 2,
  Critical: 3,
};

/** Aggregate a list of events for one actor into a compact identity summary. */
export function summarizeActorEvents(events: TraceEventModel[]): ActorSummary {
  let evidenceCount = 0;
  let highestSeverity: AlertSeverity | 'Normal' = 'Normal';
  for (const event of events) {
    if (event.evidenceRef) evidenceCount += 1;
    if (ACTOR_SEVERITY_WEIGHT[event.severity] > ACTOR_SEVERITY_WEIGHT[highestSeverity]) {
      highestSeverity = event.severity;
    }
  }
  return { spans: events.length, evidenceCount, highestSeverity };
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/trace-workbench.test.ts`
Expected: PASS（原 1 个 + 新 2 个用例）。

- [ ] **Step 5: 提交**

```bash
git add frontend/lib/trace-workbench.ts frontend/__tests__/trace-workbench.test.ts
git commit -m "feat: carry claimed roles and actor summary through the trace model"
```

---

## Task 4: 追踪页责任链路四触点 + 左侧进程树统一

**Files:**

- Modify: `frontend/components/traces/traces-page-client.tsx`
- Test: `frontend/__tests__/traces-page-client.test.tsx`

**Interfaces:**

- Consumes: `AgentAvatar`（Task 2）、`agentColor`（Task 1）、`summarizeActorEvents`/`active.actorRoles`（Task 3）
- Produces: 责任链路分组头/事件行/展开详情/右侧面板的 agent 身份呈现；Critical 严重度强调

- [ ] **Step 1: 写失败测试（扩展既有测试文件）**

在 `frontend/__tests__/traces-page-client.test.tsx` 的 `describe` 内追加：

```tsx
it('renders agent identity (role badge + avatar) in the responsibility chain', () => {
  render(<TracesPageClient />, { wrapper: createWrapper() });

  // role badge derived from the alert's claimed_role
  expect(screen.getByText('security-auditor')).toBeDefined();
  // at least one agent avatar (aria-label = the agent id)
  expect(screen.getAllByLabelText(/agent-/).length).toBeGreaterThan(0);
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/traces-page-client.test.tsx`
Expected: FAIL（`getByText('security-auditor')` 找不到）。

- [ ] **Step 3: 实现（`traces-page-client.tsx`）**

**a) 导入**：lucide 导入去掉 `Server`；新增：

```tsx
import AgentAvatar from '@/components/ui/agent-avatar';
import { agentColor } from '@/lib/agent-identity';
import {
  buildTraceRunModel,
  summarizeActorEvents,
  type TraceEventKind,
  type TraceEventModel,
  type TraceRunModel,
} from '@/lib/trace-workbench';
```

**b) `SpanRow` 事件行**——在 kind 图标之后、标题之前加迷你头像（第 91 行 `<Icon size={14} />` 与标题之间）：

```tsx
          <span style={{ color: c, flexShrink: 0, display: 'flex', marginRight: 5 }}><Icon size={14} /></span>
          <AgentAvatar agentId={event.actor} size={14} />
          <span style={{ flex: 1, fontSize: T.sm, ... }}>
```

行容器（`role="button"` div）加 `position: 'relative'`，并在其第一个子元素位置（depth 指示条之前）加 Critical 左侧警示条：

```tsx
{
  event.severity === 'Critical' && (
    <span
      style={{
        position: 'absolute',
        left: -1,
        top: 5,
        bottom: 5,
        width: 2,
        borderRadius: 2,
        background: '#dc2626',
        boxShadow: '0 0 6px rgba(220,38,38,0.6)',
      }}
    />
  );
}
```

时间条（第 102 行）改为 Critical 用红色+光晕：

```tsx
<div
  style={{
    position: 'absolute',
    left: `${leftPct}%`,
    width: `max(${wPct}%, 3px)`,
    minWidth: 3,
    top: 3,
    bottom: 3,
    borderRadius: 4,
    background: event.severity === 'Critical' ? '#dc2626' : c,
    opacity: isSelected ? 0.92 : 0.65,
    boxShadow: isSelected
      ? `0 0 8px ${c}40`
      : event.severity === 'Critical'
        ? '0 0 10px rgba(220,38,38,0.55)'
        : 'none',
  }}
/>
```

**c) 展开详情**——标题行（第 110 行）之后、字段网格之前插入身份块：

```tsx
<div
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: S.sm,
    padding: '6px 8px',
    borderRadius: S.sm,
    background: 'var(--ln-surface-0)',
    border: '1px solid var(--ln-border)',
  }}
>
  <AgentAvatar agentId={event.actor} size={26} />
  <div style={{ minWidth: 0 }}>
    <div
      style={{
        fontSize: T.sm,
        fontWeight: 600,
        color: agentColor(event.actor),
        fontFamily: 'var(--ln-mono)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {event.actor}
    </div>
    {event.actorRole && (
      <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', marginTop: 1 }}>
        {event.actorRole}
      </div>
    )}
  </div>
</div>
```

同时把字段网格（第 113 行）里的 `['主体', event.actor]` 一行移除（身份已在上面呈现）。

**d) 分组头**（`renderRow` 的 `isGroupHead` 分支，第 246 行）——整体替换为：

```tsx
{
  isGroupHead &&
    (() => {
      const role = active.actorRoles[groupActor];
      const summary = summarizeActorEvents(actorGroups.get(groupActor) ?? []);
      return (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '4px 10px',
            marginTop: S.sm,
            marginBottom: 2,
            borderRadius: 6,
            background: 'var(--ln-surface-0)',
            border: '1px solid var(--ln-border)',
            borderLeft: `2px solid ${agentColor(groupActor)}`,
          }}
        >
          <AgentAvatar agentId={groupActor} size={18} />
          <span
            style={{
              fontSize: T.sm,
              fontWeight: 600,
              fontFamily: 'var(--ln-mono)',
              color: agentColor(groupActor),
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {groupActor}
          </span>
          {role && (
            <span
              style={{
                padding: '1px 7px',
                borderRadius: 999,
                fontSize: 9,
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.08)',
                color: '#d0d6e0',
                fontFamily: 'var(--ln-mono)',
                whiteSpace: 'nowrap',
              }}
            >
              {role}
            </span>
          )}
          <span
            style={{
              fontSize: 9,
              fontFamily: 'var(--ln-mono)',
              color: 'var(--ln-text-muted)',
              marginLeft: 'auto',
            }}
          >
            {summary.spans} spans
          </span>
          {summary.highestSeverity !== 'Normal' && (
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: summary.highestSeverity === 'Critical' ? '#dc2626' : '#eab308',
                flexShrink: 0,
              }}
              title={`最高 ${summary.highestSeverity}`}
            />
          )}
        </div>
      );
    })();
}
```

**e) 左侧进程树**（第 230 行）——把 `<Server size={11} style={{ color: '#60a5fa' }} />` 换成 `<AgentAvatar agentId={actor} size={14} />`（`Server` 导入已移除，无未用变量）。

**f) 右侧详情面板**（第 255 行）——在徽章行之前插入身份块：

```tsx
<div
  style={{
    display: 'flex',
    alignItems: 'center',
    gap: S.sm,
    paddingBottom: S.sm,
    borderBottom: '1px solid var(--ln-border)',
  }}
>
  <AgentAvatar agentId={sel.actor} size={28} />
  <div style={{ minWidth: 0 }}>
    <div
      style={{
        fontSize: T.sm,
        fontWeight: 600,
        color: agentColor(sel.actor),
        fontFamily: 'var(--ln-mono)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {sel.actor}
    </div>
    {sel.actorRole && (
      <div style={{ fontSize: T.xs, color: 'var(--ln-text-muted)', marginTop: 1 }}>
        {sel.actorRole}
      </div>
    )}
  </div>
</div>
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/traces-page-client.test.tsx`
Expected: PASS（原 3 个 + 新 1 个用例）。

- [ ] **Step 5: 提交**

```bash
git add frontend/components/traces/traces-page-client.tsx frontend/__tests__/traces-page-client.test.tsx
git commit -m "feat: apply agent identity system across the traces responsibility chain"
```

---

## Task 5: 共享组件统一 agent 标识

**Files:**

- Modify: `frontend/components/ui/responsibility-chain-preview.tsx`
- Modify: `frontend/components/ui/traceback-chain.tsx`

**Interfaces:**

- Consumes: `AgentAvatar`（Task 2）、`agentColor`（Task 1）
- Produces: 责任链预览的 Agent 节点、追溯链面包屑使用同一识别系统

- [ ] **Step 1: `responsibility-chain-preview.tsx`**

顶部加 `import AgentAvatar from '@/components/ui/agent-avatar';`。把节点 pill 内的图标容器（第 219-227 行）改为：`nodeType === 'Agent'` 时渲染 `AgentAvatar`，否则保留原 `Codicon` 容器：

```tsx
{
  node.nodeType === 'Agent' ? (
    <AgentAvatar agentId={node.label} size={20} />
  ) : (
    <span
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md"
      style={{
        backgroundColor: `${style.color}18`,
        border: `1px solid ${style.color}33`,
      }}
    >
      <Codicon name={style.icon} size={12} />
    </span>
  );
}
```

- [ ] **Step 2: `traceback-chain.tsx`**

顶部加 `import { agentColor } from '@/lib/agent-identity';`。把按钮（第 24-32 行）改为用 `agentColor(id)` 着色（移除固定 `text-[#818cf8]`）：

```tsx
<button
  className="font-mono text-[12px] hover:text-white/90 hover:underline"
  style={{ color: agentColor(id) }}
  onClick={() => {
    selectNode(id);
    flyToNode(id);
  }}
>
  {id.slice(0, 8)}
</button>
```

- [ ] **Step 3: 运行既有相关测试确认无回归**

Run: `cd /home/lzk/MtAgentLens/frontend && npx vitest run __tests__/`
Expected: 全部 PASS（含 traces、agent-identity、agent-avatar、trace-workbench）。

- [ ] **Step 4: 提交**

```bash
git add frontend/components/ui/responsibility-chain-preview.tsx frontend/components/ui/traceback-chain.tsx
git commit -m "feat: unify agent identity across shared chain components"
```

---

## Task 6: e2e 断言 + 全量回归门禁

**Files:**

- Modify: `frontend/e2e/compatibility-smoke.spec.ts`

- [ ] **Step 1: 扩展 smoke 断言**

在 `e2e/compatibility-smoke.spec.ts` 的 `/traces` 分支（第 30-33 行）追加 agent 头像可见性断言：

```ts
  {
    path: '/traces',
    assert: async (page: import('@playwright/test').Page) => {
      await expect(page.getByRole('link', { name: '追踪', exact: true })).toBeVisible();
      await expect(page.getByText('责任链路')).toBeVisible();
      // agent monogram avatars are part of the responsibility chain
      await expect(page.getByRole('img', { name: /agent-/ }).first()).toBeVisible();
    },
  },
```

- [ ] **Step 2: 全量回归门禁**

Run（依次执行，任一失败即停）：

```bash
cd /home/lzk/MtAgentLens/frontend
npx vitest run __tests__/          # 单测全绿
npm run type-check                 # tsc --noEmit 无错
npm run lint                       # eslint 0 error
npm run build                      # 生产构建通过
npx playwright test e2e/compatibility-smoke.spec.ts --project=chromium   # smoke 通过
```

Expected: 5 步全绿。

- [ ] **Step 3: 提交**

```bash
git add frontend/e2e/compatibility-smoke.spec.ts
git commit -m "test: assert agent avatars render in the traces responsibility chain"
```

---

## 自审对照（spec 覆盖）

| spec 要求                                                                               | 覆盖 Task |
| --------------------------------------------------------------------------------------- | --------- |
| `lib/agent-identity.ts` 纯函数（palette/fnv1a/color/initials/darken/gradient/contrast） | Task 1    |
| `AgentAvatar` 组件（role=img + aria-label + 尺寸 + 空回退）                             | Task 2    |
| `actorRoles`/`actorRole` + `summarizeActorEvents`                                       | Task 3    |
| 分组头（头像+角色徽章+spans+严重度点+边条）                                             | Task 4-d  |
| 事件行（迷你头像 + Critical 强调）                                                      | Task 4-b  |
| 展开详情（身份卡）                                                                      | Task 4-c  |
| 右侧面板（身份块）                                                                      | Task 4-f  |
| 共享组件 `responsibility-chain-preview`/`traceback-chain`                               | Task 5    |
| e2e smoke + 全量回归                                                                    | Task 6    |
| 测试策略（agent-identity/avatar/trace-workbench/traces/e2e）                            | Task 1-6  |
