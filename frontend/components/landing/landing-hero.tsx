'use client';

import { useRef } from 'react';
import Link from 'next/link';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { LandingVideoFrame } from './landing-video-frame';
import { LandingMarkReveal } from './landing-mark-reveal';
import { WordReveal } from './word-reveal';
import { landingCopy } from '@/lib/landing/landing-copy';

/**
 * ① HERO — cinematic opening：品牌开场 + 黑洞视频帧 + 惊艳 slogan + 副行 + CTA。
 *
 * 开场（~0–3.6s）是「品牌时刻」：黑洞深空底上巨大 M 缓缓画出后淡出，让位给
 * 视频 + 文案。文案错峰浮现（eyebrow → slogan 两行 → 专业副行 → CTA → 滚动提示）。
 * 主标题是「看得见 / 才可信」——超大 slogan 式两行（Apple/Linear 级），逐行
 * mask 错峰揭示；副行一句话说清产品实际价值；玻璃拟态 CTA 直达观测台。
 * 滚动时视频帧按 Zentry 帐篷形折叠让位给下一区块。
 *
 * SSR / reduced-motion：所有开场状态由 GSAP 运行时 fromTo 施加，静态 markup
 * 零内联动画态 —— 无 JS 或减少动态时视频 + 文案完整直达。
 */
export function LandingHero() {
  const eyebrowRef = useRef<HTMLParagraphElement>(null);
  const subRef = useRef<HTMLParagraphElement>(null);
  const ctaRef = useRef<HTMLAnchorElement>(null);
  const hintRef = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const eyebrow = eyebrowRef.current;
    const sub = subRef.current;
    const cta = ctaRef.current;
    const hint = hintRef.current;
    if (!eyebrow || !sub || !cta || !hint) return;
    // 文案错峰浮现 —— 品牌开场（~3.6s 黑场退场）之后，内容接力登场
    gsap.fromTo(
      eyebrow,
      { opacity: 0, y: 12 },
      { opacity: 1, y: 0, duration: 0.7, delay: 3.0, ease: 'power2.out' },
    );
    gsap.fromTo(
      sub,
      { opacity: 0, y: 14 },
      { opacity: 1, y: 0, duration: 0.7, delay: 3.9, ease: 'power2.out' },
    );
    gsap.fromTo(
      cta,
      { opacity: 0, y: 14 },
      { opacity: 1, y: 0, duration: 0.7, delay: 4.15, ease: 'power2.out' },
    );
    gsap.fromTo(hint, { opacity: 0 }, { opacity: 1, duration: 0.8, delay: 4.5, ease: 'power2.out' });
  });

  return (
    <section id="hero" className="landing-section landing-hero" aria-label="零插桩观测">
      {/* 纯 CSS 深空底 —— 视频加载前的静态兜底 */}
      <div className="landing-hero__bg" aria-hidden="true" />

      {/* 黑洞视频帧（滚动折叠让位下一区块） */}
      <LandingVideoFrame className="landing-hero__frame" />

      {/* 开场 M 品牌 logo —— 缓缓展开后淡出，不再出现 */}
      <LandingMarkReveal />

      <div className="landing-hero__content">
        <p ref={eyebrowRef} className="landing-eyebrow">
          {landingCopy.hero.eyebrow}
        </p>

        <h1 className="landing-hero__title">
          {/* 惊艳 slogan 两行 —— 逐行 mask 错峰揭示（line-by-line reveal） */}
          <WordReveal
            as="span"
            className="landing-hero__title-line"
            text={landingCopy.hero.titleLine1}
            delay={3.3}
          />
          <WordReveal
            as="span"
            className="landing-hero__title-line"
            text={landingCopy.hero.titleLine2}
            delay={3.55}
          />
        </h1>

        {/* 专业副行 —— 一句话说清产品实际价值 */}
        <p ref={subRef} className="landing-hero__sub">
          {landingCopy.hero.sub}
        </p>

        {/* 玻璃拟态 CTA —— 顶级 hero 标配行动入口 */}
        <Link ref={ctaRef} href="/console" className="landing-hero__cta" data-testid="hero-cta">
          <span>{landingCopy.hero.cta}</span>
          <svg
            className="landing-hero__cta-arrow"
            viewBox="0 0 16 16"
            aria-hidden="true"
            width="15"
            height="15"
          >
            <path
              d="M3 8 h10 M9 4 l4 4 -4 4"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </Link>
      </div>

      <div ref={hintRef} className="landing-hero__scroll-hint" data-testid="hero-scroll-hint">
        {/* 滚动光标 —— 鼠标轮廓内滚轮点持续下移（世界级滚动提示语言） */}
        <span className="landing-hero__scroll-mouse" aria-hidden="true">
          <span className="landing-hero__scroll-dot" />
        </span>
        <span className="landing-hero__scroll-label">{landingCopy.hero.scrollHint}</span>
      </div>
    </section>
  );
}
