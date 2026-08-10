// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { LandingLiveDemo } from '@/components/landing/landing-live-demo';

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

describe('LandingLiveDemo — 活责任图演示', () => {
  it('renders the heading, stage, and capability pills', () => {
    render(<LandingLiveDemo />);
    expect(screen.getByRole('heading', { name: '真实责任，即时归因' })).toBeTruthy();
    expect(screen.getByTestId('demo-graph')).toBeTruthy();
    expect(screen.getAllByLabelText('能力标签').length).toBeGreaterThan(0);
  });

  it('renders every node glyph with data-glyph and flowing particles on every edge', () => {
    const { container } = render(<LandingLiveDemo />);
    // 5 个节点（Writer / /etc/shadow / Builder / report / Policy Boundary）
    expect(container.querySelectorAll('[data-glyph]')).toHaveLength(5);
    // 每条边 3 颗 CSS offset-path 粒子
    expect(container.querySelectorAll('.demo-particle').length).toBeGreaterThanOrEqual(9);
    // 污染传播链 + 判定徽章就位
    expect(container.querySelectorAll('.demo-taint-chain')).toHaveLength(1);
    expect(container.querySelectorAll('.demo-verdict')).toHaveLength(1);
  });

  it('renders statically (particles + badges hidden via CSS) under prefers-reduced-motion', () => {
    setReducedMotion(true);
    const { container } = render(<LandingLiveDemo />);
    // 静态结构完整，图全量可见；粒子/徽章默认 opacity 0（由 CSS 控制，无 JS 也安全）
    expect(container.querySelectorAll('[data-glyph]')).toHaveLength(5);
    const firstParticle = container.querySelector('.demo-particle');
    expect(firstParticle).not.toBeNull();
    const verdict = container.querySelector('.demo-verdict');
    expect(verdict?.getAttribute('opacity')).toBe('0');
  });
});
