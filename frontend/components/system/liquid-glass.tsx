'use client';

import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import {
  applyDeepikaLiquidGlass,
  type DeepikaLiquidGlassOptions,
} from '@/lib/vendor/deepika-liquid-glass';

// Adapted from the official @sohumsuthar/liquid-glass source:
// https://github.com/sohumsuthar/liquid-glass
// We vendor the component and hook locally because the published package ships
// raw JSX entrypoints that Next.js server compilation does not transpile
// reliably in this workspace. Material architecture and behavior are preserved.

type LiquidGlassProps = {
  macro?: boolean;
  mobileFlat?: boolean;
  className?: string;
  style?: CSSProperties;
  contentClassName?: string;
  contentStyle?: CSSProperties;
  children?: ReactNode;
  engine?: 'legacy' | 'refraction';
  refractionOptions?: DeepikaLiquidGlassOptions;
};

export function LiquidGlass({
  macro = false,
  mobileFlat = false,
  className = '',
  style,
  contentClassName = '',
  contentStyle,
  children,
  engine = 'legacy',
  refractionOptions,
  ...rest
}: LiquidGlassProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const refractionKey = JSON.stringify(refractionOptions ?? null);
  const classes = ['liquid-glass'];
  if (macro) classes.push('lg-macro');
  if (mobileFlat) classes.push('lg-mobile-flat');
  if (className) classes.push(className);

  // refractionKey (JSON of refractionOptions) is the effective dependency; the
  // raw object can be an unstable reference per render, so adding it would
  // re-create the glass every frame.
  useEffect(() => {
    if (engine !== 'refraction' || !rootRef.current) return;
    const glass = applyDeepikaLiquidGlass(rootRef.current, refractionOptions);
    return () => glass.destroy();
  }, [engine, refractionKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={rootRef}
      className={classes.join(' ')}
      style={style}
      data-glass-engine={engine}
      {...rest}
    >
      {engine === 'legacy' ? <div className="liquid-glass-effect" /> : null}
      <div className="liquid-glass-tint" />
      <div className="liquid-glass-shine" />
      <div className={`liquid-glass-content ${contentClassName}`.trim()} style={contentStyle}>
        {children}
      </div>
    </div>
  );
}

export function useLiquidGlassEffects(
  opts: {
    cursor?: boolean;
    spotlight?: boolean;
    reveal?: boolean;
    scroll?: boolean;
    routeKey?: string;
  } = {},
) {
  const { cursor = true, spotlight = true, reveal = true, scroll = true, routeKey } = opts;

  useEffect(() => {
    if (!cursor && !spotlight) return;
    if (typeof window === 'undefined') return;
    if (window.matchMedia('(hover: none)').matches) return;

    const selector = '.liquid-glass, .lg-nav-btn, .lg-nav-link';
    const root = document.documentElement;
    let rafId: number | null = null;
    let lastEvent: MouseEvent | null = null;
    let wasOverGlass = false;

    const flush = () => {
      rafId = null;
      if (!lastEvent) return;
      const event = lastEvent;

      if (spotlight) {
        root.style.setProperty('--cx', `${event.clientX}px`);
        root.style.setProperty('--cy', `${event.clientY}px`);
      }

      if (cursor) {
        const closest =
          ((event.target as Element | null)?.closest?.(selector) as HTMLElement | null) ?? null;
        if (closest) {
          const rect = closest.getBoundingClientRect();
          closest.style.setProperty('--mx', `${event.clientX - rect.left}px`);
          closest.style.setProperty('--my', `${event.clientY - rect.top}px`);
        }
        const overGlass = Boolean(closest);
        if (overGlass !== wasOverGlass) {
          root.classList.toggle('over-glass', overGlass);
          wasOverGlass = overGlass;
        }
      }
    };

    const handler = (event: MouseEvent) => {
      lastEvent = event;
      if (rafId === null) rafId = requestAnimationFrame(flush);
    };

    document.addEventListener('mousemove', handler, { passive: true });
    return () => {
      document.removeEventListener('mousemove', handler);
      root.classList.remove('over-glass');
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [cursor, spotlight]);

  useEffect(() => {
    if (!reveal) return;
    if (typeof window === 'undefined' || !('IntersectionObserver' in window)) return;
    if (window.matchMedia('(hover: none)').matches) {
      document
        .querySelectorAll('.liquid-glass')
        .forEach((element) => element.setAttribute('data-reveal', 'in'));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.setAttribute('data-reveal', 'in');
            observer.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.08, rootMargin: '0px 0px -5% 0px' },
    );

    let rafId = requestAnimationFrame(() => {
      document.querySelectorAll('.liquid-glass:not([data-reveal])').forEach((element) => {
        element.setAttribute('data-reveal', '');
        observer.observe(element);
      });
      rafId = 0;
    });

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      observer.disconnect();
    };
  }, [reveal, routeKey]);

  useEffect(() => {
    if (!scroll) return;
    if (typeof window === 'undefined') return;
    const root = document.documentElement;
    const isTouch = window.matchMedia('(hover: none)').matches;
    let ticking = false;
    let lastY = window.scrollY;
    let lastT = performance.now();
    let smoothedVel = 0;
    let decayId: number | null = null;

    const update = () => {
      ticking = false;
      const y = window.scrollY;

      if (y > 16) root.classList.add('scrolled');
      else root.classList.remove('scrolled');

      if (isTouch) return;

      const t = performance.now();
      const dt = Math.max(t - lastT, 1);
      const raw = (y - lastY) / dt;
      lastY = y;
      lastT = t;

      const target = Math.max(-1, Math.min(1, raw / 2));
      smoothedVel = smoothedVel * 0.75 + target * 0.25;
      root.style.setProperty('--sv', smoothedVel.toFixed(3));
      root.style.setProperty('--svmag', Math.abs(smoothedVel).toFixed(3));

      if (decayId !== null) cancelAnimationFrame(decayId);
      const decay = () => {
        smoothedVel *= 0.85;
        root.style.setProperty('--sv', smoothedVel.toFixed(3));
        root.style.setProperty('--svmag', Math.abs(smoothedVel).toFixed(3));
        if (Math.abs(smoothedVel) > 0.005) {
          decayId = requestAnimationFrame(decay);
        } else {
          root.style.setProperty('--sv', '0');
          root.style.setProperty('--svmag', '0');
          decayId = null;
        }
      };
      decayId = requestAnimationFrame(decay);
    };

    const onScroll = () => {
      if (!ticking) {
        requestAnimationFrame(update);
        ticking = true;
      }
    };

    if (window.scrollY > 16) root.classList.add('scrolled');
    root.style.setProperty('--sv', '0');
    root.style.setProperty('--svmag', '0');

    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      root.classList.remove('scrolled');
      if (decayId !== null) cancelAnimationFrame(decayId);
    };
  }, [scroll]);
}
