'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

type Props = {
  onWidthChange: (width: number) => void;
};

export default function RailResizeOverlay({ onWidthChange }: Props) {
  const handleRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWRef = useRef(0);
  const railRef = useRef<HTMLElement | null>(null);
  const [ready, setReady] = useState(false);

  const [pos, setPos] = useState({ top: 0, left: 0, height: 0 });

  // find the incident-rail element, position handle, and track changes
  useEffect(() => {
    let rail: HTMLElement | null = null;
    let obs: ResizeObserver | null = null;

    const position = () => {
      if (!rail) return;
      const r = rail.getBoundingClientRect();
      setPos({ top: r.top, left: r.right - 7, height: r.height });
    };

    const findAndObserve = () => {
      rail = document.querySelector<HTMLElement>('.incident-rail');
      if (!rail) return false;
      railRef.current = rail;
      setReady(true);
      position();
      obs = new ResizeObserver(position);
      obs.observe(rail);
      // also track scroll
      window.addEventListener('scroll', position, true);
      window.addEventListener('resize', position);
      return true;
    };

    if (findAndObserve()) {
      return () => {
        obs?.disconnect();
        window.removeEventListener('scroll', position, true);
        window.removeEventListener('resize', position);
      };
    }

    // retry if not mounted yet
    const id = setInterval(() => { if (findAndObserve()) clearInterval(id); }, 200);
    return () => { clearInterval(id); obs?.disconnect(); };
  }, []);

  const getRail = useCallback(() => {
    return railRef.current ?? document.querySelector<HTMLElement>('.incident-rail');
  }, []);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    const rail = getRail();
    if (!rail) return;
    e.preventDefault();
    e.stopPropagation();
    draggingRef.current = true;
    startXRef.current = e.clientX;
    startWRef.current = rail.getBoundingClientRect().width;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, [getRail]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    const rail = getRail();
    if (!rail) return;
    const dx = e.clientX - startXRef.current;
    const vw = window.innerWidth;
    const maxPx = Math.min(612, Math.floor(vw * 0.8));
    const next = Math.min(maxPx, Math.max(340, startWRef.current + dx));
    rail.style.width = `${next}px`;
    rail.style.maxWidth = 'none';
  }, [getRail]);

  const onPointerUp = useCallback(() => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    const rail = getRail();
    if (!rail) return;
    const w = Math.round(rail.getBoundingClientRect().width);
    onWidthChange(w);
  }, [getRail, onWidthChange]);

  if (!ready) return null;

  return (
    <div
      ref={handleRef}
      className="rail-resize-overlay"
      style={{ top: pos.top, left: pos.left, height: pos.height }}
      role="separator"
      aria-orientation="vertical"
      aria-label="拖拽调整侧边栏宽度"
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={(e) => {
        const rail = getRail();
        if (!rail) return;
        const vw = window.innerWidth;
        const maxPx = Math.min(612, Math.floor(vw * 0.8));
        const cur = rail.getBoundingClientRect().width;
        let d = 0;
        if (e.key === 'ArrowRight') d = 20;
        else if (e.key === 'ArrowLeft') d = -20;
        else return;
        e.preventDefault();
        const next = Math.min(maxPx, Math.max(340, cur + d));
        rail.style.width = `${next}px`;
        rail.style.maxWidth = 'none';
        onWidthChange(next);
      }}
    >
      <span className="rail-resize-overlay__grip" />
    </div>
  );
}
