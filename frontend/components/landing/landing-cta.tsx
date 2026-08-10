'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import MtlLogo from '@/components/ui/mtl-logo';
import { FrameMask } from './frame-mask';
import { WordReveal } from './word-reveal';
import { computeTilt } from '@/lib/landing/tilt';
import { landingCopy } from '@/lib/landing/landing-copy';
import { landingScroller } from '@/lib/landing/scroll';
import { LANDING_REVEAL } from '@/lib/landing/reveal';
import { usePauseOffscreen } from '@/lib/landing/pause-offscreen';

/** 控制台实时事件流 —— 观测台「是活的」的视觉证据（装饰，aria-hidden） */
const LIVE_EVENTS = [
  '[eBPF]   tls_capture · TLS 明文 +1.2 KB',
  '[Rust]   污点 · /etc/shadow → builder',
  '[Observe] 判定 · AUTHORITATIVE',
];

/**
 * Final call-to-action — the observatory portal.
 *
 *  - A LIVE console event ticker above the button: three pipeline events fade
 *    through one another (GSAP loop), the way the real SSE stream keeps
 *    writing — "this observatory is alive".
 *  - A giant magnetic "enter the console" portal button: a conic ring slowly
 *    rotates around the amber pill, it leans toward the cursor (Zentry-style),
 *    the label swaps 进入观测台 ↔ OBSERVE on hover, and a click ripples.
 *  - The tech-stack mono line + a minimal footer.
 *
 * Reduced-motion / no-JS: the ticker shows its first event statically and the
 * button stays still — everything is visible, nothing loops.
 */
export function LandingCta() {
  const router = useRouter();
  const [hovered, setHovered] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLElement>(null);
  // 离屏暂停：conic ring 旋转 + halo 呼吸的持续动画在离开视口时暂停
  usePauseOffscreen(rootRef);

  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const q = gsap.utils.selector(root);

      // 滚动进入时的逐元素错峰渐入 —— 与其他 section 统一的 scrub 绑定
      // （FrameMask 整块渐入 + 内部 ticker/按钮/页脚随滚动进度依次升起）
      const reveal = gsap.timeline({
        scrollTrigger: {
          trigger: root,
          start: LANDING_REVEAL.start,
          end: LANDING_REVEAL.end,
          scrub: LANDING_REVEAL.scrub,
          scroller: landingScroller(),
        },
      });
      reveal
        .fromTo(q('.landing-cta__ticker'), { opacity: 0, y: 14 }, { opacity: 1, y: 0, ease: 'power2.out' }, 0.15)
        .fromTo(q('.ln-btn--portal'), { opacity: 0, y: 20 }, { opacity: 1, y: 0, ease: 'power2.out' }, 0.35)
        .fromTo(q('.landing-cta__stack, .landing-footer'), { opacity: 0 }, { opacity: 1, ease: 'power2.out' }, 0.55);

      // ticker 内三条事件的无限轮换（reveal 后开始可见）。
      // 原位交叉淡化（不位移）：之前 y±12 会把文字推出 22px 容器与 heading
      // 重叠、截断跳动——顶级「事件流」是原位淡出/淡入接替。
      const ticks = q('.landing-cta__tick');
      if (ticks.length) {
        const tt = gsap.timeline({ repeat: -1 });
        ticks.forEach((tick, i) => {
          tt.fromTo(
            tick,
            { opacity: 0 },
            { opacity: 1, duration: 0.45, ease: 'power2.out' },
            i * 2,
          ).to(tick, { opacity: 0, duration: 0.45, ease: 'power2.in' }, i * 2 + 1.55);
        });
        return () => {
          reveal.scrollTrigger?.kill();
          reveal.kill();
          tt.kill();
        };
      }
      return () => {
        reveal.scrollTrigger?.kill();
        reveal.kill();
      };
    },
    { scope: rootRef },
  );

  // Magnetic 3D tilt — the portal leans toward the cursor (Zentry-style).
  const handleMove = (e: React.MouseEvent<HTMLButtonElement>) => {
    const el = btnRef.current;
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const rect = el.getBoundingClientRect();
    const { rotateX, rotateY } = computeTilt({ x: e.clientX, y: e.clientY }, rect);
    el.style.transform = `perspective(800px) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
  };

  const handleLeave = () => {
    const el = btnRef.current;
    if (!el) return;
    el.style.transform = '';
  };

  return (
    <section
      ref={rootRef}
      className="landing-section landing-cta anim-inview"
      aria-label="进入观测台"
    >
      {/* FrameMask 竖抹揭示 —— 与其他 section 统一渐入（CTA 之前缺此包裹，
          导致 ticker/按钮/页脚滚动进入时直接全部呈现） */}
      <FrameMask>
        <WordReveal as="h2" className="landing-cta__heading" text={landingCopy.cta.heading} />

        {/* 实时事件流 —— 观测台「活着」的证据 */}
        <div className="landing-cta__ticker" aria-hidden="true">
          {LIVE_EVENTS.map((ev) => (
            <span key={ev} className="landing-cta__tick">
              {ev}
            </span>
          ))}
        </div>

      <button
        ref={btnRef}
        type="button"
        data-testid="landing-cta"
        data-hovered={hovered}
        className="ln-btn ln-btn--primary ln-btn--giant ln-btn--portal"
        onMouseEnter={() => setHovered(true)}
        onMouseMove={handleMove}
        onMouseLeave={() => {
          setHovered(false);
          handleLeave();
        }}
        onClick={() => router.push('/console')}
      >
        <span className="ln-btn__labels">
          <span className="ln-btn__label ln-btn__label--default">{landingCopy.cta.button}</span>
          <span className="ln-btn__label ln-btn__label--roll" aria-hidden="true">
            {landingCopy.cta.buttonHover}
          </span>
        </span>
        <svg
          className="ln-btn__arrow"
          viewBox="0 0 32 32"
          aria-hidden="true"
          width="26"
          height="26"
        >
          <path
            d="M6 16 h20 M18 8 l8 8 -8 8"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      <p className="landing-cta__stack">{landingCopy.cta.stack}</p>

        <footer className="landing-footer">
          <MtlLogo size={14} mono />
          <span>{landingCopy.cta.footer}</span>
        </footer>
      </FrameMask>
    </section>
  );
}
