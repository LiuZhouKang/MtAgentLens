'use client';

import { useCallback, useRef } from 'react';
import { computeTilt } from '@/lib/landing/tilt';
import { cn } from '@/lib/utils';

/**
 * BentoTilt — a card that tilts in 3D toward the cursor (Zentry-style).
 *
 * Pointer position relative to the card center maps to rotateX/rotateY clamped
 * to ±10° with a gentle scale lift (pure math in `computeTilt`). Transform is
 * applied directly to the DOM node via a motion-safe composite property.
 * Reduced-motion disables the tilt entirely.
 */
export function BentoTilt({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  const ref = useRef<HTMLDivElement>(null);

  const handleMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const rect = el.getBoundingClientRect();
    const { rotateX, rotateY, scale } = computeTilt({ x: e.clientX, y: e.clientY }, rect);
    el.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale(${scale})`;
    // Spotlight 光斑跟随鼠标 —— 消费卡片的 --spot-x/--spot-y（::after 光斑定位）
    el.style.setProperty('--spot-x', `${e.clientX - rect.left}px`);
    el.style.setProperty('--spot-y', `${e.clientY - rect.top}px`);
  }, []);

  const handleLeave = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.transform = '';
  }, []);

  return (
    <div
      ref={ref}
      className={cn('benti-tilt', className)}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      style={{ transformStyle: 'preserve-3d', willChange: 'transform' }}
      {...rest}
    >
      {children}
    </div>
  );
}
