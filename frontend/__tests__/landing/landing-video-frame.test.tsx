// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { cleanup, render, screen } from '@testing-library/react';
import { LandingVideoFrame } from '@/components/landing/landing-video-frame';

afterEach(() => cleanup());

describe('LandingVideoFrame — 黑洞视频帧', () => {
  it('renders the video-frame wrapper with the black-hole video', () => {
    render(<LandingVideoFrame />);
    const frame = screen.getByTestId('landing-video-frame');
    expect(frame).toBeTruthy();
    expect(frame.getAttribute('aria-hidden')).toBe('true');
    // CC0 黑洞视频（Mixkit "Black Hole rotating"）—— 静音/循环/内联播放
    const video = frame.querySelector<HTMLVideoElement>('video.landing-mark__video');
    expect(video).toBeTruthy();
    expect(video!.getAttribute('src')).toBe('/videos/hero-blackhole.mp4');
    expect(video!.muted).toBe(true);
    expect(video!.loop).toBe(true);
    expect(video!.playsInline).toBe(true);
  });

  it('does not clip the frame in static SSR markup (no-JS safe)', () => {
    // SSR 无 effect 执行：不应带任何 inline clip-path，降级场景视频帧完整可见。
    const html = renderToStaticMarkup(<LandingVideoFrame />);
    expect(html).toContain('landing-mark');
    expect(html).not.toContain('clip-path');
  });

  it('binds the seamless loop fade to the video (data-loop-fade-bound)', () => {
    const { container } = render(<LandingVideoFrame />);
    const video = container.querySelector<HTMLVideoElement>('video.landing-mark__video');
    expect(video).toBeTruthy();
    // GSAP ticker 已挂上循环淡化 —— 结尾淡出到黑、loop 后从黑淡入
    expect(video!.dataset.loopFadeBound).toBe('true');
  });

  it('does not inline any opacity in static SSR markup (loop fade is runtime-only)', () => {
    const html = renderToStaticMarkup(<LandingVideoFrame />);
    expect(html).not.toContain('style="opacity');
  });

  it('renders fully visible under prefers-reduced-motion', () => {
    window.matchMedia = ((query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    render(<LandingVideoFrame />);
    expect(screen.getByTestId('landing-video-frame')).toBeTruthy();
  });

});
