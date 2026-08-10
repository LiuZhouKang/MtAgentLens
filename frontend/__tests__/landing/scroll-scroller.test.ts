// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { landingScroller } from '@/lib/landing/scroll';

/**
 * landingScroller — the landing page scrolls inside `.landing-root` (a
 * viewport-height overflow container), NOT the window: the console's global
 * `html/body/#app-main { overflow: hidden }` rules leave the document itself
 * unscrollable, so every landing ScrollTrigger must target that container.
 */
describe('landingScroller', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('resolves the .landing-root container when present', () => {
    const root = document.createElement('div');
    root.className = 'landing-root';
    document.body.appendChild(root);

    expect(landingScroller()).toBe(root);
  });

  it('falls back to the document element when no .landing-root exists (unit tests / SSR shells)', () => {
    expect(landingScroller()).toBe(document.documentElement);
  });

  it('ignores lookalike classes and resolves the real .landing-root', () => {
    const decoy = document.createElement('section');
    decoy.className = 'landing-root-decoy';
    document.body.appendChild(decoy);

    const root = document.createElement('div');
    root.className = 'landing-root';
    document.body.appendChild(root);

    expect(landingScroller()).toBe(root);
  });
});
