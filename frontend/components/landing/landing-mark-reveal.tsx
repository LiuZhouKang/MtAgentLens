'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import MtlLogo from '@/components/ui/mtl-logo';
import { landingCopy } from '@/lib/landing/landing-copy';

/**
 * 开场品牌时刻 — THE PRODUCT'S OWN living MtlLogo, emerging from deep space.
 *
 * 用户明确要求：开场 logo 必须是「我们专门设计好的顶级 MtlLogo」——即产品原生
 * 的 animated MtlLogo（琥珀渐变轨道 + 琥珀核心 + 原生呼吸/雷达脉冲/轨道流动）。
 * 因此这里用 `<MtlLogo animated />`，它的原生 seamless 动画全程自行运行；
 * GSAP 只编排容器级的深空浮现（scale + translateZ + blur）、wordmark 签名与
 * 退场——绝不碰 SVG 内部，也不叠加任何会扭曲 logo 外观的自定义效果。
 *
 * 五段编排（GSAP timeline，position 参数对齐）：
 *  ① emerge   logo 容器从深空拉近（translateZ -320→0 + scale 0.5→1 + blur 溶解）
 *             —— 期间 MtlLogo 原生动画持续呼吸/脉冲
 *  ② sign     wordmark「MtAgentLens」上升溶解落定（不用 letter-spacing 撑开，
 *             防窄屏横向溢出）
 *  ③ hold     驻留
 *  ④ collapse 品牌坍缩回深空（微缩 + 模糊 + 淡出）—— 能量被吸入黑洞
 *
 * Decorative (`aria-hidden`)。开场状态全部 GSAP 运行时 fromTo 施加：
 * 静态 markup 零内联动画态。prefers-reduced-motion 下整个 overlay 隐藏。
 */
export function LandingMarkReveal({ onComplete }: { onComplete?: () => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);
  const wordRef = useRef<HTMLParagraphElement>(null);

  useGSAP(
    () => {
      const root = rootRef.current;
      const logo = logoRef.current;
      const word = wordRef.current;
      if (!root || !logo || !word) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        root.style.display = 'none';
        return;
      }

      const tl = gsap.timeline({ onComplete });
      // ── ① 深空浮现 —— 3D 相机拉近（Zentry Spline 语言），容器级，不碰 SVG 内部
      tl.fromTo(
        logo,
        { scale: 0.5, translateZ: -320, opacity: 0, filter: 'blur(14px)' },
        { scale: 1, translateZ: 0, opacity: 1, filter: 'blur(0px)', duration: 1.3, ease: 'power4.out' },
        0,
      );

      // ── ② 签名 —— wordmark 上升溶解落定 ──
      tl.fromTo(
        word,
        { opacity: 0, y: 16, filter: 'blur(8px)' },
        { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.7, ease: 'power2.out' },
        1.6,
      );

      // ── ③ 退场 —— 品牌先完全消失（logo + wordmark 微缩淡出），随后黑场渐隐、
      //    黑洞从黑中显现。MtlLogo 原生动画随之随容器一起淡出。
      tl.to(logo, { opacity: 0, scale: 0.92, translateZ: -40, filter: 'blur(4px)', duration: 0.35, ease: 'power2.in' }, 2.7)
        .to(word, { opacity: 0, y: -8, duration: 0.35, ease: 'power2.in' }, 2.7)
        .to(root, { opacity: 0, duration: 0.55, ease: 'power2.inOut' }, 3.05)
        .set(root, { display: 'none' }, 3.6);

      return () => tl.kill();
    },
    { scope: rootRef },
  );

  return (
    <div
      ref={rootRef}
      data-testid="landing-mark-reveal"
      aria-hidden="true"
      className="landing-mark-reveal"
    >
      <div className="landing-brand-reveal">
        {/* 产品原生 MtlLogo —— animated 原生呼吸/脉冲/轨道流动全程自行运行 */}
        <div ref={logoRef} className="landing-brand-reveal__logo">
          <MtlLogo animated size={240} className="landing-brand-reveal__mtl" />
        </div>
        <p ref={wordRef} className="landing-brand-reveal__wordmark">
          {landingCopy.brand.wordmark}
        </p>
      </div>
    </div>
  );
}
