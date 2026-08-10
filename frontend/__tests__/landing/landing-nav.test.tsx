// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { LandingNav } from '@/components/landing/landing-nav';
import { landingCopy } from '@/lib/landing/landing-copy';

afterEach(() => cleanup());

describe('LandingNav — 顶部导航', () => {
  it('renders brand + one anchor link per copy entry with # hrefs', () => {
    render(<LandingNav />);
    expect(screen.getByTestId('landing-nav')).toBeTruthy();
    for (const link of landingCopy.nav.links) {
      const a = screen.getByRole('link', { name: link.label });
      expect(a.getAttribute('href')).toBe(`#${link.id}`);
    }
  });

  it('nav has no redundant CTA button (2026-08-10: hero/底部已有「进入观测台」)', () => {
    render(<LandingNav />);
    expect(screen.queryByTestId('landing-nav-cta')).toBeNull();
    expect(screen.queryByText('进入观测台')).toBeNull();
  });
});
