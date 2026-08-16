import { describe, expect, it } from 'vitest';
import { demoGraph, landingCopy, splitIntoWords } from '@/lib/landing/landing-copy';

describe('landing-copy — 六区块文案结构', () => {
  it('has no placeholder strings anywhere', () => {
    const json = JSON.stringify(landingCopy).toLowerCase();
    for (const bad of ['tbd', 'todo', 'lorem', 'xxx', 'placeholder', 'undefined']) {
      expect(json).not.toContain(bad);
    }
  });

  it('nav: anchors present, no redundant CTA (2026-08-10 去掉与 hero/底部重复的入口)', () => {
    expect(landingCopy.nav.links.length).toBeGreaterThanOrEqual(3);
    expect(landingCopy.nav.links[0].id).toBe('pipeline');
    expect((landingCopy.nav as { cta?: string }).cta).toBeUndefined();
  });

  it('hero: eyebrow + slogan title + professional sub + CTA + scroll hint', () => {
    expect(landingCopy.hero.eyebrow).toContain('eBPF');
    // 惊艳 slogan 两行（无彩色强调）：大字 = 信任结论「看得见 / 才信赖」
    expect(landingCopy.hero.titleLine1).toBe('看得见');
    expect(landingCopy.hero.titleLine2).toBe('才信赖');
    expect(landingCopy.hero.titleLine1.endsWith('，')).toBe(false); // 不以逗号收尾
    // 专业副行：四层价值一句话（跨层可观测 → 风险可预警）
    expect(landingCopy.hero.sub).toContain('跨层可观测');
    expect(landingCopy.hero.sub).toContain('风险可预警');
    // hero CTA
    expect(landingCopy.hero.cta.length).toBeGreaterThan(0);
    expect(landingCopy.hero.scrollHint.length).toBeGreaterThan(0);
  });

  it('brand: wordmark + tagline', () => {
    expect(landingCopy.brand.wordmark).toBe('MtAgentLens');
    expect(landingCopy.brand.tagline.length).toBeGreaterThan(0);
  });

  it('pipeline: exactly three named layers (not numbered), each with tags', () => {
    const layers = landingCopy.pipeline.layers;
    expect(layers).toHaveLength(3);
    const ids = layers.map((l) => l.id);
    expect(ids).toContain('ebpf');
    expect(ids).toContain('rust');
    expect(ids).toContain('observe');
    for (const layer of layers) {
      expect(layer.name.length).toBeGreaterThan(0);
      expect(layer.desc.length).toBeGreaterThan(0);
      expect(layer.tags.length).toBeGreaterThan(0);
    }
  });

  it('demo: heading + capabilities', () => {
    expect(landingCopy.demo.heading.length).toBeGreaterThan(0);
    expect(landingCopy.demo.capabilities).toHaveLength(4);
    expect(landingCopy.demo.capabilities).toContain('责任图');
    expect(landingCopy.demo.capabilities).toContain('取证链');
  });

  it('values: three pillars numbered 01/02/03', () => {
    const pillars = landingCopy.values.pillars;
    expect(pillars).toHaveLength(3);
    expect(pillars.map((p) => p.index)).toEqual(['01', '02', '03']);
    for (const p of pillars) {
      expect(p.title.length).toBeGreaterThan(0);
      expect(p.desc.length).toBeGreaterThan(0);
    }
  });

  it('cta: button + rollover swap text + stack tags', () => {
    expect(landingCopy.cta.button).toBe('进入观测台');
    expect(landingCopy.cta.buttonHover.length).toBeGreaterThan(0);
    expect(landingCopy.cta.stack).toContain('eBPF');
    expect(landingCopy.cta.footer).toContain('MtAgentLens');
  });
});

describe('demoGraph — 迷你责任图快照', () => {
  it('contains 3–5 nodes covering the pollution → propagation → attribution arc', () => {
    expect(demoGraph.nodes.length).toBeGreaterThanOrEqual(3);
    expect(demoGraph.nodes.length).toBeLessThanOrEqual(5);
    const types = new Set(demoGraph.nodes.map((n) => n.node_type));
    expect(types.has('Agent')).toBe(true);
    expect(types.has('Resource')).toBe(true);
    expect(types.has('PolicyBoundary')).toBe(true);
  });

  it('every edge endpoint references an existing node', () => {
    const ids = new Set(demoGraph.nodes.map((n) => n.node_id));
    expect(demoGraph.edges.length).toBeGreaterThan(0);
    for (const e of demoGraph.edges) {
      expect(ids.has(e.source)).toBe(true);
      expect(ids.has(e.target)).toBe(true);
      expect(e.confidence).toBeGreaterThan(0);
      expect(e.confidence).toBeLessThanOrEqual(1);
    }
  });

  it('contains at least one taint-propagation edge (责任传播语义)', () => {
    expect(demoGraph.edges.some((e) => e.edge_type === 'TaintPropagation')).toBe(true);
  });

  it('is deterministic across calls', () => {
    expect(demoGraph).toEqual(demoGraph);
  });
});

describe('splitIntoWords — 逐词/逐字拆分', () => {
  it('splits latin text by spaces', () => {
    expect(splitIntoWords('OBSERVE THE FLOW')).toEqual(['OBSERVE', 'THE', 'FLOW']);
  });

  it('splits CJK text into individual characters', () => {
    expect(splitIntoWords('零插桩')).toEqual(['零', '插', '桩']);
  });

  it('handles mixed CJK + latin segments', () => {
    expect(splitIntoWords('零插桩 看见')).toEqual(['零', '插', '桩', '看', '见']);
    expect(splitIntoWords('零插桩 OBSERVE')).toEqual(['零', '插', '桩', 'OBSERVE']);
  });

  it('keeps latin words whole', () => {
    expect(splitIntoWords('MtAgentLens')).toEqual(['MtAgentLens']);
  });

  it('returns [] for empty / whitespace-only input', () => {
    expect(splitIntoWords('')).toEqual([]);
    expect(splitIntoWords('   ')).toEqual([]);
  });

  it('trims surrounding whitespace', () => {
    expect(splitIntoWords('  OBSERVE  FLOW  ')).toEqual(['OBSERVE', 'FLOW']);
  });
});
