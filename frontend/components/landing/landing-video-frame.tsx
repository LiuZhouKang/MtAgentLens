'use client';

import { useEffect, useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { clipPathStyle, markBorderRadius } from '@/lib/landing/mark-morph';
import { landingScroller } from '@/lib/landing/scroll';
import { loopFadeOpacity } from '@/lib/landing/video-loop-fade';
import { cn } from '@/lib/utils';

gsap.registerPlugin(ScrollTrigger);

/**
 * Hero 视频帧 — Zentry `#video-frame` 等价物（全屏黑洞视频）。
 *
 * 承载 hero 背景视频（CC0 黑洞素材）与深空叠层。滚动时视频帧按
 * `clipPathStyle` / `markBorderRadius` 折成 Zentry「帐篷形」——顶部窄带 +
 * 两侧斜边，优雅让位给下一区块。M 品牌 logo 由 `LandingMarkReveal`
 * 在开场独立演绎并退场，不驻留本帧。
 *
 * Decorative (`aria-hidden`)。No-JS / prefers-reduced-motion 渲染完整可见：
 * 开场状态全部由 GSAP 运行时施加，静态 markup 零内联动画态。
 */
export function LandingVideoFrame({ className }: { className?: string }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const sizeRef = useRef({ w: 0, h: 0 });

  // Keep the current viewport size for the fold morph (recomputed on resize).
  useEffect(() => {
    const measure = () => {
      sizeRef.current.w = window.innerWidth;
      sizeRef.current.h = window.innerHeight;
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, []);

  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      // 滚动折叠 — video-frame clip-path + borderRadius morph（Zentry morph）。
      // 节奏对齐 Zentry：帧中心进入视口中心才开折叠，帧底到视口中心时折成
      // 「帐篷形」，优雅让位给下一区块。
      const st = ScrollTrigger.create({
        trigger: root,
        start: 'center center',
        end: 'bottom center',
        scrub: true,
        scroller: landingScroller(),
        onUpdate: (self) => {
          const { w, h } = sizeRef.current;
          if (!w || !h) return;
          // 滚动回顶（progress 0）时清掉 inline 折叠态 —— 与「开场后零内联
          // 动画态」一致，computed clip 恢复 none，e2e 初始断言也更稳健
          if (self.progress <= 0) {
            root.style.clipPath = '';
            root.style.borderRadius = '';
            return;
          }
          root.style.clipPath = clipPathStyle(w, h, self.progress);
          root.style.borderRadius = markBorderRadius(self.progress);
        },
      });

      return () => st.kill();
    },
    { scope: rootRef },
  );

  // 无缝循环 —— 素材首尾帧不匹配，直接 loop 硬切会突兀。GSAP ticker 逐帧
  // 按 `loopFadeOpacity` 写视频 opacity：结尾渐隐进黑、loop 跳回后从黑渐显，
  // 黑洞黑场正好遮蔽循环缝（吸积盘「溶解 → 浮现」）。静态 markup 零内联态。
  useGSAP(
    () => {
      const video = videoRef.current;
      if (!video) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      if (video.dataset.loopFadeBound === 'true') return; // StrictMode 双绑保护
      video.dataset.loopFadeBound = 'true';

      const BASE_OPACITY = 0.92; // 与 .landing-mark__video CSS opacity 一致
      const tick = () => {
        const d = video.duration;
        if (!Number.isFinite(d) || d <= 0) return; // metadata 未就绪 —— 保持完整可见
        const fade = Math.min(1.2, d * 0.18); // 淡窗：短素材等比缩短，长素材封顶 1.2s
        video.style.opacity = String(loopFadeOpacity(video.currentTime, d, fade) * BASE_OPACITY);
      };
      const id = gsap.ticker.add(tick);
      return () => gsap.ticker.remove(id);
    },
    { scope: rootRef },
  );

  return (
    <div
      ref={rootRef}
      data-testid="landing-video-frame"
      aria-hidden="true"
      className={cn('landing-mark', className)}
    >
      {/* CC0 黑洞视频背景 — video-frame 内容（滚动时随容器折角折叠） */}
      <video
        ref={videoRef}
        className="landing-mark__video"
        src="/videos/hero-blackhole.mp4"
        autoPlay
        muted
        loop
        playsInline
        preload="metadata"
        aria-hidden="true"
        tabIndex={-1}
      />
      {/* 深空渐变叠层 — 保证视频不糊住文字（reduced-motion 时替代视频静态兜底） */}
      <div className="landing-mark__scrim" aria-hidden="true" />
      {/* 底部暖光晕影 */}
      <div className="landing-mark__halo" aria-hidden="true" />
    </div>
  );
}
