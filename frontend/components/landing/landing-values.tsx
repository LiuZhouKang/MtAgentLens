'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { FrameMask } from './frame-mask';
import { WordReveal } from './word-reveal';
import { landingCopy } from '@/lib/landing/landing-copy';
import { landingScroller } from '@/lib/landing/scroll';
import { LANDING_REVEAL } from '@/lib/landing/reveal';

gsap.registerPlugin(ScrollTrigger);

/**
 * ⑤ The value pillars — 01 零插桩 / 02 责任归因 / 03 取证链.
 *
 * Numbered markers are true here: the pillars read as a sequence of promises
 * the product keeps, one per line — 01 through 03.
 */
export function LandingValues() {
  const listRef = useRef<HTMLOListElement>(null);

  useGSAP(
    () => {
      const list = listRef.current;
      if (!list) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      // Pillars rise in sequence — clean y+opacity (no rotationX 3D flip:
      // rotateX 的 from-state 会加宽投影在窄屏撑出横向溢出)。scrub 绑定随滚动渐入。
      gsap.fromTo(
        list.querySelectorAll<HTMLElement>('.landing-values__pillar'),
        { opacity: 0, y: 36 },
        {
          opacity: 1,
          y: 0,
          ease: 'none',
          stagger: 0.12,
          scrollTrigger: {
            trigger: list,
            start: LANDING_REVEAL.start,
            end: LANDING_REVEAL.end,
            scrub: LANDING_REVEAL.scrub,
            scroller: landingScroller(),
          },
        },
      );
    },
    { scope: listRef },
  );

  return (
    <section id="values" className="landing-section landing-values" aria-labelledby="values-title">
      <FrameMask>
        <div className="landing-section__head">
          <WordReveal as="h2" id="values-title" text={landingCopy.values.heading} />
        </div>

        <ol ref={listRef} className="landing-values__list">
          {landingCopy.values.pillars.map((pillar) => (
            <li key={pillar.index} className="landing-values__pillar" data-index={pillar.index}>
              <span className="landing-values__index" aria-hidden="true">
                {pillar.index}
              </span>
              <div className="landing-values__body">
                <h3 className="landing-values__title">{pillar.title}</h3>
                <p className="landing-values__desc">{pillar.desc}</p>
              </div>
            </li>
          ))}
        </ol>

        {/* 权威证明条 —— 真实系统能力规格 */}
        <dl className="landing-values__proof" aria-label="真实系统能力">
          {landingCopy.values.proof.map((item) => (
            <div key={item} className="landing-values__proof-item">
              <dt aria-hidden="true">▸</dt>
              <dd>{item}</dd>
            </div>
          ))}
        </dl>
      </FrameMask>
    </section>
  );
}
