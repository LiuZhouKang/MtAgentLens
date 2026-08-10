/**
 * Landing page copy + the mini responsibility-graph snapshot + pure text utils.
 *
 * `landingCopy` is the single source of truth for every string on the landing entry page /.
 * `demoGraph` is a static inline snapshot of a real pollution→propagation→
 * attribution arc (sourced from the responsibility-graph semantics) — the
 * landing page renders it without touching the store or any data source.
 */

import type { ResponsibilityEdge, ResponsibilityNode } from '@/lib/types';

// ─── 文案 ────────────────────────────────────────────────────────────────

export const landingCopy = {
  nav: {
    // 2026-08-10：nav 只做导航，不再有 CTA（去掉与 hero/底部重复的「进入观测台」）
    links: [
      { id: 'pipeline', label: '三层管线' },
      { id: 'demo', label: '真实演示' },
      { id: 'values', label: '为什么' },
    ],
  },
  hero: {
    eyebrow: 'eBPF · RUST · NEXT.JS — 零插桩观测',
    // 惊艳 slogan 式两行（Apple/Linear 级）：看得见（观测）→ 才可信（责任归因/审计）。
    // 全白大字，无彩色强调。逐行错峰 reveal。
    titleLine1: '看得见',
    titleLine2: '才可信',
    // 专业副行：一句话说清产品实际价值（零插桩 + 看见 what/why + 责任归因）
    sub: '内核级零插桩 · 看见每个 Agent 做了什么、为什么 —— 责任归因，一目了然。',
    cta: '进入观测台',
    scrollHint: '向下滚动探索',
  },
  brand: {
    wordmark: 'MtAgentLens',
    tagline: '零插桩，看见每个 Agent 做了什么、为什么。',
  },
  pipeline: {
    heading: '一条管线，三层深度',
    subheading: '从内核字节到责任判定，全程可溯。',
    layers: [
      {
        id: 'ebpf',
        name: 'eBPF 采集层',
        desc: '内核级零插桩探针，拦截 SSL/TLS 明文与进程事件，不改一行业务代码。',
        tags: ['tls_capture', 'browsertrace', 'stdiocap'],
      },
      {
        id: 'rust',
        name: 'Rust 归因层',
        desc: '流式框架实时归因：责任图、污点追踪、身份血缘，权威判定谁做了什么。',
        tags: ['ResponsibilityGraph', 'Runtime'],
      },
      {
        id: 'observe',
        name: '观测呈现层',
        desc: 'Next.js 交互观测：责任图、取证链、告警调查、时间演化回放。',
        tags: ['Overview', 'Investigations', 'Traces'],
      },
    ],
  },
  demo: {
    heading: '真实责任，即时归因',
    subheading: '一个污染源如何穿越协作链传播，最终被权威判定——这是真实的迷你责任图快照。',
    capabilities: ['责任图', '取证链', '告警调查', '图时间演化'],
  },
  values: {
    heading: '为什么是 MtAgentLens',
    // 权威证明条 —— 全部来自真实系统能力（非营销数字）
    proof: ['4 种 eBPF 内核探针', '3 信号身份血缘', '2 阶段责任判定', '双写持久化 · SQLite + JSONL'],
    pillars: [
      {
        index: '01',
        title: '零插桩',
        desc: '探针挂载于内核的 SSL 读写与进程事件之上，拦截 TLS 明文、文件操作、网络与 MCP stdio——不改一行业务代码、不注入 SDK，Agent 对观测毫无感知。',
      },
      {
        index: '02',
        title: '责任归因',
        desc: '责任图、污点追踪与三信号身份血缘（root_pid / TLS 会话 / 会话 ID）层层合并：谁读了什么、污染了谁、最终责任落在哪个 Agent——给出可解释的权威判定。',
      },
      {
        index: '03',
        title: '取证链',
        desc: '证据关联、Traceback 回溯与双写持久化（SQLite + JSONL）：每次判定都可下钻到原始事件、回放到那一刻，经得起审计与复盘。',
      },
    ],
  },
  cta: {
    heading: '进入观测台',
    button: '进入观测台',
    buttonHover: 'OBSERVE',
    stack: 'eBPF · Rust 2024 · Next.js 16 · React 19',
    footer: 'MtAgentLens © 2026 · 零插桩 LLM Agent 可观测性',
  },
};

// ─── 迷你责任图快照 ──────────────────────────────────────────────────────

// 固定基准时间（保证 SSR/CSR 与多次渲染确定性）
const BASE_NS = Date.parse('2026-06-20T09:30:00.000Z') * 1_000_000;

export interface MiniGraph {
  nodes: ResponsibilityNode[];
  edges: ResponsibilityEdge[];
}

export const demoGraph: MiniGraph = {
  nodes: [
    {
      node_id: 'agent-writer',
      node_type: 'Agent',
      agent_id: 'agent-writer',
      resource: null,
      evidence_refs: ['ev-1'],
      timestamp_ns: BASE_NS,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
    {
      node_id: 'res-shadow',
      node_type: 'Resource',
      agent_id: null,
      resource: { File: { canonical_path: '/etc/shadow' } },
      evidence_refs: ['ev-1'],
      timestamp_ns: BASE_NS,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
    {
      node_id: 'agent-builder',
      node_type: 'Agent',
      agent_id: 'agent-builder',
      resource: null,
      evidence_refs: ['ev-2'],
      timestamp_ns: BASE_NS,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
    {
      node_id: 'res-report',
      node_type: 'Resource',
      agent_id: null,
      resource: { File: { canonical_path: '/docs/report.md' } },
      evidence_refs: ['ev-2'],
      timestamp_ns: BASE_NS,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
    {
      node_id: 'boundary-system',
      node_type: 'PolicyBoundary',
      agent_id: null,
      resource: null,
      evidence_refs: ['ev-3'],
      timestamp_ns: BASE_NS,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
  ],
  edges: [
    {
      edge_id: 'e-read',
      edge_type: 'IntentCausesAction',
      source: 'agent-writer',
      target: 'res-shadow',
      evidence_refs: ['ev-1'],
      confidence: 0.92,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
    {
      edge_id: 'e-taint',
      edge_type: 'TaintPropagation',
      source: 'res-shadow',
      target: 'agent-builder',
      evidence_refs: ['ev-1', 'ev-2'],
      confidence: 0.87,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
    {
      edge_id: 'e-write',
      edge_type: 'CapabilityUse',
      source: 'agent-builder',
      target: 'res-report',
      evidence_refs: ['ev-2'],
      confidence: 0.9,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
    {
      edge_id: 'e-cross',
      edge_type: 'PolicyBoundaryCrossing',
      source: 'res-shadow',
      target: 'boundary-system',
      evidence_refs: ['ev-3'],
      confidence: 0.84,
      phase: { Authoritative: { timestamp_ns: BASE_NS, overrides: null } },
    },
  ],
};

// ─── 逐词拆分 ───────────────────────────────────────────────────────────

const CJK_RE = /[一-鿿㐀-䶿]/;

/**
 * Split a title line into reveal units for the word-by-word animation.
 *
 * Latin tokens stay whole (split on whitespace); CJK text has no spaces, so each
 * character becomes its own unit (零插桩 → 零/插/桩) for a dramatic per-char
 * stagger. Separators like · are kept as standalone units.
 */
export function splitIntoWords(text: string): string[] {
  const tokens = text.trim().split(/\s+/).filter(Boolean);
  const words: string[] = [];
  for (const token of tokens) {
    if (CJK_RE.test(token)) {
      for (const ch of token) {
        if (ch.trim()) words.push(ch);
      }
    } else {
      words.push(token);
    }
  }
  return words;
}
