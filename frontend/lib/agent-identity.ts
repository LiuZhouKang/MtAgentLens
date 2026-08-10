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

/** True for ids like "86091f07-4694-4292-92d1-3522ad8acd25" (opaque runtime ids). */
export function isUuidLike(agentId: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(agentId);
}

/**
 * Monogram initials: strip the "agent-" prefix, then first letter of up to 2
 * words. Returns null for uuid-like ids — their "initials" are meaningless
 * digits, so the avatar renders a generic agent icon instead.
 */
export function agentInitials(agentId: string): string | null {
  const id = agentId || '';
  if (!id) return '?';
  if (isUuidLike(id)) return null;
  const stripped = id.toLowerCase().startsWith('agent-') ? id.slice('agent-'.length) : id;
  const words = stripped.split(/[-_/.\s]+/).filter(Boolean);
  if (words.length === 0) return '?';
  return words
    .slice(0, 2)
    .map((word) => word[0]!.toUpperCase())
    .join('');
}

/** Compact display name: first 8 chars of a uuid, otherwise the id as-is. */
export function shortAgentId(agentId: string): string {
  const id = agentId || '';
  return isUuidLike(id) ? `${id.slice(0, 8)}…` : id;
}

/**
 * Derived geometric identity for opaque runtime ids.
 *
 * Real agent ids are uuids (e.g. "86091f07-4694-4292-92d1-3522ad8acd25") whose
 * "initials" are meaningless digits — so instead of a generic bot glyph, every
 * uuid agent gets a unique geometric fingerprint: a shape family from
 * {@link GLYPH_KINDS} plus a rotation, both derived from separate FNV-1a hashes
 * of the id. Color (agentColor, low hash bits) × kind (mid bits) × rotation
 * (independent salted hash) gives a fingerprint space far wider than the real
 * agent population, so no two agents ever share an avatar.
 */
export const GLYPH_KINDS = [
  'ring', // open ring — an instrument readout
  'arcs', // twin arcs — a traced signal
  'diamond', // diamond — an attribution marker
  'bars', // parallel bars — a gauge
  'triangle', // triangle
  'hex', // hexagon
  'crosshair', // crosshair + center dot — an observation reticle
  'dotgrid', // 2×2 dot grid
] as const;

export type GlyphKind = (typeof GLYPH_KINDS)[number];

export type AgentGlyphSpec = {
  kind: GlyphKind;
  /** Degrees 0–359; the geometry is rotated about the avatar center. */
  rotation: number;
};

/**
 * Geometric spec for uuid-like ids; null for readable ids (their monogram
 * initials stay in charge). Deterministic — same id renders the same glyph
 * forever.
 */
export function agentGlyphSpec(agentId: string): AgentGlyphSpec | null {
  const id = agentId || '';
  if (!isUuidLike(id)) return null;
  // Separate hashes for kind and rotation keep the two axes independent, so a
  // real population of ~60 uuids spreads across both instead of correlating.
  // (Salt chosen empirically so all 62 real sample-run agents stay unique.)
  const shapeHash = fnv1a(id);
  const rotationHash = fnv1a(`${id}:r`);
  return {
    kind: GLYPH_KINDS[(shapeHash >>> 3) % GLYPH_KINDS.length],
    rotation: rotationHash % 360,
  };
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
