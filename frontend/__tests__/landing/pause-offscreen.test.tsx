// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { useRef } from 'react';
import { usePauseOffscreen } from '@/lib/landing/pause-offscreen';
import { LANDING_REVEAL } from '@/lib/landing/reveal';

function Host({ cls }: { cls: string }) {
  const ref = useRef<HTMLElement>(null);
  usePauseOffscreen(ref);
  // 真实 section 静态 markup 自带 anim-inview（no-JS 兜底），测试同样模拟
  return <section ref={ref} className={`${cls} anim-inview`} data-testid="host" />;
}

type IOCB = (entries: Array<{ isIntersecting: boolean }>) => void;

describe('usePauseOffscreen — 离屏持续动画暂停', () => {
  let cb: IOCB | null = null;

  beforeEach(() => {
    cb = null;
    const Ctor = vi.fn().mockImplementation(function (this: unknown, callback: IOCB) {
      cb = callback;
      return {
        observe: vi.fn(),
        unobserve: vi.fn(),
        disconnect: vi.fn(),
      };
    });
    vi.stubGlobal('IntersectionObserver', Ctor);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('元素进入视口 → 加回 anim-inview（动画恢复）', () => {
    const { getByTestId } = render(<Host cls="landing-demo" />);
    const el = getByTestId('host');
    // 初始静态 markup 自带 anim-inview（no-JS 兜底）
    expect(el.classList.contains('anim-inview')).toBe(true);
    // IO 首次回调：离屏 → 移除（暂停）
    cb?.([{ isIntersecting: false }]);
    expect(el.classList.contains('anim-inview')).toBe(false);
    // 进入视口 → 加回（恢复）
    cb?.([{ isIntersecting: true }]);
    expect(el.classList.contains('anim-inview')).toBe(true);
  });

  it('先离屏再进入视口：class 往返切换正确', () => {
    const { getByTestId } = render(<Host cls="landing-pipeline" />);
    const el = getByTestId('host');
    cb?.([{ isIntersecting: false }]);
    expect(el.classList.contains('anim-inview')).toBe(false);
    cb?.([{ isIntersecting: true }]);
    cb?.([{ isIntersecting: false }]);
    expect(el.classList.contains('anim-inview')).toBe(false);
  });

  it('IntersectionObserver 不可用时保持 class 不动（动画照常运行）', () => {
    vi.unstubAllGlobals();
    vi.stubGlobal('IntersectionObserver', undefined);
    const { getByTestId } = render(<Host cls="landing-cta" />);
    expect(getByTestId('host').classList.contains('anim-inview')).toBe(true);
  });
});

describe('LANDING_REVEAL — 顶级 scrub 滚动渐入（回归守卫）', () => {
  it('section 一进视口（top 92%）即开始渐入，不做慢等待', () => {
    expect(LANDING_REVEAL.start).toBe('top 92%');
  });

  it('滚动区间合理（~37% 视口内完成），scrub 平滑跟随', () => {
    expect(LANDING_REVEAL.end).toBe('top 55%');
    expect(typeof LANDING_REVEAL.scrub).toBe('number');
  });

  it('时间触发场景（hero）的揭示时长保持在 0.5–0.7s', () => {
    expect(LANDING_REVEAL.wordRevealDuration).toBeLessThanOrEqual(0.7);
    expect(LANDING_REVEAL.itemDuration).toBeLessThanOrEqual(0.6);
  });
});
