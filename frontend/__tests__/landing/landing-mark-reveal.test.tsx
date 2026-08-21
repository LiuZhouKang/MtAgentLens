// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { cleanup, render, screen } from '@testing-library/react';
import { LandingMarkReveal } from '@/components/landing/landing-mark-reveal';

afterEach(() => cleanup());

describe('LandingMarkReveal — 品牌开场（MtlLogo + wordmark）', () => {
  it('renders the brand reveal: MtlLogo orbit mark + MtAgentLens wordmark', () => {
    const { container } = render(<LandingMarkReveal />);
    const reveal = screen.getByTestId('landing-mark-reveal');
    expect(reveal).toBeTruthy();
    expect(reveal.getAttribute('aria-hidden')).toBe('true');
    // 品牌核心标志（MtlLogo 的轨道结构）在场
    expect(container.querySelector('.landing-brand-reveal__mtl')).toBeTruthy();
    expect(container.querySelector('.mtl-orbit')).toBeTruthy();
    expect(container.querySelector('.mtl-core')).toBeTruthy();
    // wordmark 签名在场
    expect(screen.getByText('MtAgentLens')).toBeTruthy();
  });

  it('does not set any inline animation state in static SSR markup (no-JS: brand statically visible)', () => {
    // SSR 无 effect 执行：不应带任何内联动画态（transform/opacity/filter/stroke-dashoffset）
    const html = renderToStaticMarkup(<LandingMarkReveal />);
    expect(html).toContain('mtl-orbit');
    expect(html).toContain('MtAgentLens');
    expect(html).not.toContain('stroke-dashoffset');
    // 无内联动画态（transform:/opacity:/filter: 属性）——MtlLogo 静态布局
    // 的 transform-box/origin 是设计值，不在此列
    expect(html).not.toMatch(/style="[^"]*(transform:|opacity:|filter:)/);
  });

  it('hides the brand reveal entirely under prefers-reduced-motion', () => {
    window.matchMedia = ((q: string) => ({
      matches: q.includes('prefers-reduced-motion'),
      media: q,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    render(<LandingMarkReveal />);
    // 品牌开场不播放 —— hero 黑洞 + 文案完整直达
    expect(screen.getByTestId('landing-mark-reveal').style.display).toBe('none');
  });
});
