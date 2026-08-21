// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { cleanup, render, screen } from '@testing-library/react';
import { FrameMask } from '@/components/landing/frame-mask';

function setReducedMotion(matches: boolean) {
  window.matchMedia = ((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

afterEach(() => cleanup());

describe('FrameMask — 几何转场蒙版', () => {
  it('renders its children inside the mask wrapper', () => {
    render(
      <FrameMask>
        <p>section content</p>
      </FrameMask>,
    );
    const el = screen.getByTestId('frame-mask');
    expect(el.textContent).toContain('section content');
  });

  it('does not clip content in static SSR markup (no-JS safe)', () => {
    // SSR（renderToStaticMarkup 无 effect 执行）不应带任何 inline clip-path，
    // 这样无 JS / 爬虫 / 降级场景内容默认完全可见。
    const html = renderToStaticMarkup(<FrameMask>visible</FrameMask>);
    expect(html).toContain('visible');
    expect(html).not.toContain('clip-path');
  });

  it('stays unclipped (content visible) under prefers-reduced-motion', () => {
    setReducedMotion(true);
    render(<FrameMask>visible</FrameMask>);
    const el = screen.getByTestId('frame-mask');
    expect(el.style.clipPath).toBe('');
    expect(el.textContent).toContain('visible');
  });
});
