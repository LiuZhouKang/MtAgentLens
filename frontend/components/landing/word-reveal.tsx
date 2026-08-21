'use client';

import { useMemo, useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { splitIntoWords } from '@/lib/landing/landing-copy';
import { landingScroller } from '@/lib/landing/scroll';
import { LANDING_REVEAL } from '@/lib/landing/reveal';
import { cn } from '@/lib/utils';

gsap.registerPlugin(ScrollTrigger);

/**
 * Word-by-word reveal (Zentry AnimatedTitle-style cascade).
 *
 * Splits a title line into reveal units — latin tokens stay whole, CJK text
 * becomes one unit per character (see `splitIntoWords`). Each `.word-inner`
 * starts slightly below and transparent, and rises into place on scroll with a
 * staggered `power4.out` — a clean, robust cascade. Pure transform/opacity
 * (compositor-friendly, no per-word Gaussian blur repaint), triggered the
 * moment the line enters the viewport (top 90%) for a snappy, lag-free feel.
 *
 * Deliberately NO 3D perspective flip (`rotateX/Y` about a negative z origin
 * under `perspective`). That pose renders as a diagonal sliver — the upper
 * half of each glyph missing — on real browsers/GPUs, and it is the single
 * most fragile place on the page. A vertical rise can never clip a glyph.
 *
 * Accessibility: the animated word spans are `aria-hidden` (decorative), so the
 * intact line is also rendered as an `sr-only` sibling — screen readers read the
 * full text once, no `aria-label` on the wrapper (which axe flags on roleless
 * elements like `<span>`).
 * Reduced-motion / no-JS: no animation is ever applied — the initial pose is
 * applied by GSAP `fromTo` at runtime (never in static CSS), so text is fully
 * visible even with JS disabled.
 */
export type WordRevealProps = {
  text: string;
  className?: string;
  wordClassName?: string;
  as?: 'span' | 'p' | 'h1' | 'h2' | 'h3';
  /** Forwarded to the root tag (e.g. `id` for aria-labelledby anchors). */
  id?: string;
  /** Only animate on the first scroll into view (default), not on every pass. */
  once?: boolean;
  /** Seconds to wait before the reveal starts (e.g. let the opening M logo play first). */
  delay?: number;
  /** 逐词精确匹配的"重点词"——匹配的词加 `word--accent`（amber 渐变强调）。 */
  accent?: string[];
};

export function WordReveal({
  text,
  className,
  wordClassName,
  as = 'span',
  id,
  once = true,
  delay = 0,
  accent = [],
}: WordRevealProps) {
  const ref = useRef<HTMLElement>(null);
  const words = useMemo(() => splitIntoWords(text), [text]);

  useGSAP(
    () => {
      const el = ref.current;
      if (!el) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const words = el.querySelectorAll<HTMLElement>('.word-inner');
      if (delay > 0) {
        // 时间触发（hero 主标题：品牌开场后按 delay 错峰登场）
        gsap.fromTo(
          words,
          { y: '120%' },
          {
            y: '0%',
            duration: LANDING_REVEAL.wordRevealDuration,
            stagger: LANDING_REVEAL.wordRevealStagger,
            ease: 'power4.out',
            delay,
          },
        );
      } else {
        // 滚动 scrub 渐入（section 标题）：词随滚动进度逐词升起
        // （mask-line 揭示，Linear/Stripe 签名式标题语言，只动 transform）。
        // 快滚快现、慢滚慢现、回滚淡出——与滚动对话。
        gsap.fromTo(
          words,
          { y: '120%' },
          {
            y: '0%',
            ease: 'none',
            stagger: LANDING_REVEAL.wordRevealStagger,
            scrollTrigger: {
              trigger: el,
              start: LANDING_REVEAL.start,
              end: LANDING_REVEAL.end,
              scrub: LANDING_REVEAL.scrub,
              scroller: landingScroller(),
            },
          },
        );
      }
    },
    { scope: ref, dependencies: [once, delay] },
  );

  const Tag = as as React.ElementType;

  const CJK_RE = /[一-鿿㐀-䶿]/;

  return (
    <Tag ref={ref as React.Ref<HTMLElement>} id={id} className={cn('word-reveal', className)}>
      {words.map((w, i) => {
        const next = words[i + 1];
        // CJK 字紧跟在 latin 词前时补足 proper 间距。splitIntoWords 会把
        // 「看见 Agent」之间的空格吞掉，而逐字 margin 只有 0.02em，会挤成
        // 「看见Agent」。仅当后一个是真 latin 词（含 a-z）才加 0.3em 间距；
        // CJK 标点（，。等）保持紧跟，不误加。
        const needsGap = CJK_RE.test(w) && !!next && /[a-zA-Z]/.test(next);
        return (
          <span
            key={`${i}-${w}`}
            className={cn(
              'word',
              CJK_RE.test(w) && 'word--cjk',
              needsGap && 'word--cjk-gap',
              accent.includes(w) && 'word--accent',
              wordClassName,
            )}
            aria-hidden="true"
          >
            <span className="word-inner">{w}</span>
          </span>
        );
      })}
      <span className="sr-only">{text}</span>
    </Tag>
  );
}
