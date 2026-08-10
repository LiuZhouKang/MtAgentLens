'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { landingScroller } from '@/lib/landing/scroll';
import { LANDING_REVEAL } from '@/lib/landing/reveal';
import { cn } from '@/lib/utils';

gsap.registerPlugin(ScrollTrigger);

/**
 * FrameMask — 滚动 scrub 渐入的 section 统一揭示层（2026-08-10 scrub 化）。
 *
 * 之前是 once clip-path 竖抹（弹完就停）；现按顶级规范改为 **scrub 绑定**：
 * 内容随滚动进度从下方升起 + 淡入（opacity + y，合成器友好，无逐帧重绘），
 * 快滚快现、慢滚慢现、回滚淡出——滚动全程与内容对话。
 *
 * 从状态（opacity 0 + y 44）只由 GSAP 运行时 fromTo 施加 —— 静态 markup
 * 零内联动画态，no-JS / prefers-reduced-motion 下内容完整可见。
 */
export function FrameMask({
  className,
  children,
  start = LANDING_REVEAL.start,
  end = LANDING_REVEAL.end,
  scrub = LANDING_REVEAL.scrub,
  'data-testid': testId = 'frame-mask',
}: {
  className?: string;
  children?: React.ReactNode;
  start?: string;
  end?: string;
  scrub?: number | boolean;
  'data-testid'?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const el = ref.current;
      if (!el) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      gsap.fromTo(
        el,
        { opacity: 0, y: 44 },
        {
          opacity: 1,
          y: 0,
          ease: 'none',
          scrollTrigger: {
            trigger: el,
            start,
            end,
            scrub,
            scroller: landingScroller(),
          },
        },
      );
    },
    { scope: ref },
  );

  return (
    <div ref={ref} data-testid={testId} className={cn('frame-mask', className)}>
      {children}
    </div>
  );
}
