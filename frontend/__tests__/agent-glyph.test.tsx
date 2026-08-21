// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import AgentGlyph from '@/components/ui/agent-glyph';
import { GLYPH_KINDS, type AgentGlyphSpec } from '@/lib/agent-identity';

afterEach(() => cleanup());

function glyphFor(kind: AgentGlyphSpec['kind'], rotation: number): string | null {
  cleanup(); // avoid querySelector picking up a previous render's leftover svg
  render(<AgentGlyph spec={{ kind, rotation }} size={18} />);
  return document.querySelector('[data-agent-glyph]')?.getAttribute('data-kind') ?? null;
}

describe('AgentGlyph', () => {
  it.each(GLYPH_KINDS)('renders a white-stroked svg for the %s kind', (kind) => {
    render(<AgentGlyph spec={{ kind, rotation: 45 }} size={18} />);
    const svg = document.querySelector<SVGElement>('[data-agent-glyph]');
    expect(svg).not.toBeNull();
    // geometry is inside the 24×24 viewBox — strokes (paths) or dots (circles)
    expect(svg!.querySelectorAll('path, circle').length).toBeGreaterThan(0);
  });

  it('renders every glyph library kind without crashing', () => {
    for (const kind of GLYPH_KINDS) {
      expect(glyphFor(kind, 30)).toBe(kind);
    }
  });

  it('applies the rotation to an inner transform group', () => {
    render(<AgentGlyph spec={{ kind: 'ring', rotation: 123 }} size={18} />);
    const group = document.querySelector<SVGGElement>('g[transform]');
    expect(group).not.toBeNull();
    expect(group!.getAttribute('transform')).toContain('123');
  });

  it('is aria-hidden — the accessible name comes from the owning avatar', () => {
    render(<AgentGlyph spec={{ kind: 'diamond', rotation: 0 }} size={18} />);
    expect(document.querySelector('[data-agent-glyph]')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('renders at the requested pixel size', () => {
    render(<AgentGlyph spec={{ kind: 'hex', rotation: 30 }} size={14} />);
    const svg = document.querySelector('[data-agent-glyph]');
    expect(svg?.getAttribute('width')).toBe('14');
    expect(svg?.getAttribute('height')).toBe('14');
  });
});
