import { describe, expect, test } from 'vitest';
import {
  AGENT_COLOR_OVERRIDES,
  AGENT_PALETTE,
  agentColor,
  agentGradient,
  agentGlyphSpec,
  agentInitials,
  contrastWhiteOn,
  darkenHex,
  fnv1a,
  GLYPH_KINDS,
  isUuidLike,
  shortAgentId,
} from '@/lib/agent-identity';

// Real opaque runtime ids from public/data/sample-run.json — these are the
// ids the responsibility chain renders in production, so the glyph system
// must keep them mutually distinguishable.
const REAL_UUIDS = [
  '0040b355-f7a7-4874-a410-e39390325d4c',
  '005a18e9-e9f8-4c9d-90e3-828c503f9f22',
  '0088f15d-556a-434f-bd17-1995e74b3190',
  '00995be7-9440-42e5-a0d7-1c1607d9f5f0',
  '009fdd4c-37be-43b4-8c23-38d516d32fa2',
  '00b4551d-3ab3-4589-ad90-49da8517530d',
  '00f5f4fb-5ff4-41ab-aa71-ed8d0f633bb1',
  '0133626e-94f5-4565-9e4c-1681d1adc345',
  '015645a1-012e-4786-b40b-e37c0e17aaf8',
  '019b6e92-7e7c-4bdb-933e-5fe9dc9b9304',
  '01a1cad6-e126-4d4a-951a-110b056b60fb',
  '01a46f6c-9d97-4352-a63c-b38c35e0cdc6',
];

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

  test('uuid-like ids yield no initials (render a generic icon instead)', () => {
    expect(isUuidLike('86091f07-4694-4292-92d1-3522ad8acd25')).toBe(true);
    expect(agentInitials('86091f07-4694-4292-92d1-3522ad8acd25')).toBeNull();
    expect(isUuidLike('agent-security')).toBe(false);
    expect(isUuidLike('not-a-uuid')).toBe(false);
  });

  test('shortAgentId abbreviates uuids but keeps readable ids', () => {
    expect(shortAgentId('86091f07-4694-4292-92d1-3522ad8acd25')).toBe('86091f07…');
    expect(shortAgentId('agent-security')).toBe('agent-security');
    expect(shortAgentId('')).toBe('');
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

describe('agentGlyphSpec — derived geometric identity for opaque runtime ids', () => {
  test('returns null for readable ids (their monogram initials stay in charge)', () => {
    expect(agentGlyphSpec('agent-security')).toBeNull();
    expect(agentGlyphSpec('unknown')).toBeNull();
    expect(agentGlyphSpec('')).toBeNull();
  });

  test('uuid ids get a stable spec drawn from the glyph library', () => {
    const uuid = '86091f07-4694-4292-92d1-3522ad8acd25';
    const a = agentGlyphSpec(uuid);
    const b = agentGlyphSpec(uuid);
    expect(a).not.toBeNull();
    expect(GLYPH_KINDS).toContain(a!.kind);
    expect(a!.rotation).toBeGreaterThanOrEqual(0);
    expect(a!.rotation).toBeLessThan(360);
    expect(a).toEqual(b); // deterministic — same id, same glyph forever
  });

  test('real sample-run uuids are mutually distinguishable (not one generic glyph)', () => {
    const specs = REAL_UUIDS.map((id) => agentGlyphSpec(id)!);
    expect(specs.every((s) => s !== null)).toBe(true);
    // color (8) × kind (8) × rotation (360) — the fingerprint space is far
    // wider than the real population, so no two of 62 agents should collide.
    const fingerprints = new Set(specs.map((s) => `${s.kind}|${s.rotation}`));
    expect(fingerprints.size).toBe(REAL_UUIDS.length);
    // and the shape family itself is not degenerate (more than one kind used)
    expect(new Set(specs.map((s) => s.kind)).size).toBeGreaterThan(1);
  });
});
