// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { LandingHero } from '@/components/landing/landing-hero';

afterEach(() => cleanup());

describe('LandingHero — HERO 区块', () => {
  it('renders eyebrow, headline (h1), english sub-line and scroll hint', () => {
    render(<LandingHero />);
    // eyebrow
    expect(screen.getByText(/零插桩观测/)).toBeTruthy();
    // h1 逐词标题（惊艳 slogan 两行）
    expect(screen.getByRole('heading', { level: 1 })).toBeTruthy();
    expect(screen.getAllByText('看得见').length).toBeGreaterThan(0);
    expect(screen.getAllByText('才可信').length).toBeGreaterThan(0);
    // 专业副行 + 玻璃 CTA
    expect(screen.getByText(/责任归因/)).toBeTruthy();
    expect(screen.getByTestId('hero-cta')).toBeTruthy();
    // 滚动提示
    expect(screen.getByTestId('hero-scroll-hint')).toBeTruthy();
  });

  it('anchors the hero on the black-hole video-frame + opening M logo', () => {
    const { container } = render(<LandingHero />);
    // 视频帧容器（替代旧粒子层）
    expect(screen.getByTestId('landing-video-frame')).toBeTruthy();
    // 纯 CSS 深空背景（视频加载前的静态兜底）
    expect(container.querySelector('.landing-hero__bg')).toBeTruthy();
    // CC0 黑洞视频背景在场，且静音/循环/内联播放
    const video = container.querySelector<HTMLVideoElement>('video.landing-mark__video');
    expect(video).toBeTruthy();
    expect(video!.getAttribute('src')).toBe('/videos/hero-blackhole.mp4');
    expect(video!.muted).toBe(true);
    expect(video!.loop).toBe(true);
    expect(video!.playsInline).toBe(true);
    expect(video!.getAttribute('aria-hidden')).toBe('true');
    // 深空渐变叠层在场（保底对比度）
    expect(container.querySelector('.landing-mark__scrim')).toBeTruthy();
    // 开场品牌时刻（LandingMarkReveal）在场 —— MtlLogo 深空浮现后淡出，不驻留
    const reveal = screen.getByTestId('landing-mark-reveal');
    expect(reveal).toBeTruthy();
    expect(container.querySelector('.landing-brand-reveal__mtl .mtl-orbit')).toBeTruthy();
    expect(screen.getByText('MtAgentLens')).toBeTruthy();
    // 不再有常驻右下角 M / 孪生 M
    expect(container.querySelector('[data-testid="hero-letter"]')).toBeNull();
    expect(container.querySelector('.landing-hero__letter-ghost')).toBeNull();
    // 不再有粒子 canvas / 能量核心 —— 黑洞视频帧纯粹干净
    expect(container.querySelector('canvas')).toBeNull();
  });

  it('renders statically (no crash) under prefers-reduced-motion', () => {
    window.matchMedia = ((query: string) => ({
      matches: query.includes('prefers-reduced-motion') && true,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    render(<LandingHero />);
    expect(screen.getByRole('heading', { level: 1 })).toBeTruthy();
    expect(screen.getByTestId('landing-video-frame')).toBeTruthy();
    // reduced-motion：开场 M logo 不出现，内容完整直达
    expect(screen.getByTestId('landing-mark-reveal').style.display).toBe('none');
  });
});
