'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import MtlLogo from '@/components/ui/mtl-logo';
import { FrameMask } from './frame-mask';
import { landingCopy } from '@/lib/landing/landing-copy';
import { landingScroller } from '@/lib/landing/scroll';
import { LANDING_REVEAL } from '@/lib/landing/reveal';

gsap.registerPlugin(ScrollTrigger);

/**
 * ② Brand moment — THE SAME full-dynamic MtlLogo as the hero opening.
 *
 * 用户最终澄清：这个区块（「一条管线，三层深度」上方的 MtAgentLens / 零插桩
 * 区块）里的 logo，必须和开场 reveal 完全同款——动态效果拉满的 MtlLogo：
 *
 *  ① emerge  logo 容器从深空拉近（scale 0.5→1 + translateZ -320→0 +
 *            blur 14→0 + opacity），期间 animated MtlLogo 的原生呼吸/雷达
 *            脉冲/轨道流动全程自行运行（与开场完全同款）
 *  ② sign    wordmark「MtAgentLens」上升溶解落定
 *  ③ settle  tagline 落位
 *  ④ hold    驻留（logo 持续是「活」的，不坍缩）
 *
 * `animated` MtlLogo 的原生动画不会在大尺寸错位（svgOrigin 缩放原点在核心
 * 中心 256,256，变换矩阵的 translate 是正确编码）。容器级 transform 不碰
 * SVG 内部，因此原生 logo 外观零扭曲。Reduced-motion / no-JS：无 tween，
 * 静态完整可见。
 */
export function LandingBrandMoment() {
  const rootRef = useRef<HTMLElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);
  const wordRef = useRef<HTMLHeadingElement>(null);
  const tagRef = useRef<HTMLParagraphElement>(null);

  useGSAP(
    () => {
      const root = rootRef.current;
      const logo = logoRef.current;
      const word = wordRef.current;
      const tag = tagRef.current;
      if (!root || !logo || !word || !tag) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const tl = gsap.timeline({
        // section 一进视口（top 85%）即触发深空浮现 —— 快而跟手
        scrollTrigger: { trigger: root, start: 'top 85%', once: true, scroller: landingScroller() },
      });

      // ① 深空浮现 —— 与开场 reveal 完全同款（容器级，不碰 SVG 内部）。
      // blur 只在这一个容器上（depth-of-field 签名，单 filter 层开销可控），
      // 时长从 1.3s 压到 0.9s，消除慢等待。
      tl.fromTo(
        logo,
        { scale: 0.5, translateZ: -320, opacity: 0, filter: 'blur(14px)' },
        { scale: 1, translateZ: 0, opacity: 1, filter: 'blur(0px)', duration: 0.9, ease: 'power4.out' },
        0,
      );

      // ② wordmark 上升落定 —— 纯 y+opacity（合成器友好，不用 blur 逐帧重绘）
      tl.fromTo(
        word,
        { opacity: 0, y: 22 },
        { opacity: 1, y: 0, duration: LANDING_REVEAL.itemDuration, ease: 'power3.out' },
        0.9,
      );

      // ③ tagline 落位
      tl.fromTo(
        tag,
        { opacity: 0, y: 14 },
        { opacity: 1, y: 0, duration: 0.5, ease: 'power2.out' },
        '-=0.3',
      );

      // ④ 滚动视差 —— 品牌内容整体随滚动穿过轻微上漂（深度感）。
      // 独立于 ① 的一次性浮现（父容器 parallax + 子元素 reveal 变换合成，
      // 互不覆盖），scrub: 0.5 平滑跟随滚轮。
      const inner = root.querySelector('.landing-brand__inner');
      const parallax = inner
        ? gsap.to(inner, {
            yPercent: -6,
            ease: 'none',
            scrollTrigger: {
              trigger: root,
              start: 'top bottom',
              end: 'bottom top',
              scrub: 0.5,
              scroller: landingScroller(),
            },
          })
        : null;

      // ⑤ 驻留 —— 不坍缩，logo 持续是「活」的
      return () => {
        tl.scrollTrigger?.kill();
        tl.kill();
        parallax?.scrollTrigger?.kill();
        parallax?.kill();
      };
    },
    { scope: rootRef },
  );

  return (
    <section ref={rootRef} className="landing-section landing-brand" aria-label="品牌">
      <FrameMask>
        <div className="landing-brand__inner">
          <div ref={logoRef} className="landing-brand__logo">
            {/* 与开场同款：产品原生 animated MtlLogo，深空浮现动态效果拉满 */}
            <MtlLogo animated size={260} />
          </div>
          <h2 ref={wordRef} className="landing-brand__wordmark">
            {landingCopy.brand.wordmark}
          </h2>
          <p ref={tagRef} className="landing-brand__tagline">
            {landingCopy.brand.tagline}
          </p>
        </div>
      </FrameMask>
    </section>
  );
}
