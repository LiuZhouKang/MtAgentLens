'use client';

import { useEffect, useRef, useState } from 'react';
import MtlLogo from '@/components/ui/mtl-logo';
import { landingCopy } from '@/lib/landing/landing-copy';
import { landingScroller } from '@/lib/landing/scroll';
import { cn } from '@/lib/utils';

/**
 * Top navigation for the landing page — brand + section anchors only.
 *
 * 2026-08-10：去掉 nav 的 CTA 按钮——首屏 nav 与 hero 两个「进入观测台」
 * 重复冗余（顶级规范：nav 只做导航，主 CTA 由 hero 承担，底部 CTA 负责
 * 内容后转化）。品牌 + 锚点链接保持简洁。滚动时隐藏/上滑重现。
 */
export function LandingNav() {
  const [hidden, setHidden] = useState(false);
  const lastY = useRef(0);

  // The landing page scrolls inside `.landing-root`, not the window — track
  // that container so hide-on-scroll reacts to real user scrolls.
  useEffect(() => {
    const scroller = landingScroller();
    const onScroll = () => {
      const y = scroller.scrollTop;
      setHidden(y > 140 && y > lastY.current);
      lastY.current = y;
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => scroller.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <header
      data-testid="landing-nav"
      className={cn('landing-nav', hidden && 'landing-nav--hidden')}
    >
      <a href="#hero" className="landing-nav__brand" aria-label="MtAgentLens">
        <MtlLogo size={20} mono />
        <span className="landing-nav__brand-name">MtAgentLens</span>
      </a>

      <nav className="landing-nav__links" aria-label="区块导航">
        {landingCopy.nav.links.map((link) => (
          <a key={link.id} href={`#${link.id}`} className="landing-nav__link">
            {link.label}
          </a>
        ))}
      </nav>
    </header>
  );
}
