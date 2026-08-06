'use client';

import { useCallback, useRef, useState } from 'react';

type Props = {
  railEl: HTMLElement | null;
  onResize: (width: number) => void;
};

const MIN_W = 340;
const MAX_PX = 612;
const MAX_VW = 0.8;

export default function RailResizeHandle({ railEl, onResize }: Props) {
  const [dragging, setDragging] = useState(false);
  const lastXRef = useRef(0);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragging(true);
      lastXRef.current = e.clientX;
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    },
    [],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging) return;
      const dx = e.clientX - lastXRef.current;
      lastXRef.current = e.clientX;
      if (!railEl) return;
      const vw = window.innerWidth;
      const max = Math.min(MAX_PX, Math.floor(vw * MAX_VW));
      const currentW = railEl.getBoundingClientRect().width;
      const next = Math.min(max, Math.max(MIN_W, currentW + dx));
      onResize(next);
    },
    [dragging, railEl, onResize],
  );

  const onPointerUp = useCallback(() => {
    setDragging(false);
  }, []);

  const keyHandler = useCallback(
    (e: React.KeyboardEvent) => {
      if (!railEl) return;
      const vw = window.innerWidth;
      const max = Math.min(MAX_PX, Math.floor(vw * MAX_VW));
      const currentW = railEl.getBoundingClientRect().width;
      let delta = 0;
      if (e.key === 'ArrowRight') delta = 20;
      else if (e.key === 'ArrowLeft') delta = -20;
      else if (e.key === 'ArrowUp') delta = 40;
      else if (e.key === 'ArrowDown') delta = -40;
      else return;
      e.preventDefault();
      const next = Math.min(max, Math.max(MIN_W, currentW + delta));
      onResize(next);
    },
    [railEl, onResize],
  );

  if (!railEl) return null;

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="拖拽调整侧边栏宽度"
      aria-valuenow={Math.round(railEl.getBoundingClientRect().width)}
      aria-valuemin={MIN_W}
      aria-valuemax={Math.min(MAX_PX, Math.floor(window.innerWidth * MAX_VW))}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={keyHandler}
      className="rail-resize-handle"
      style={{ touchAction: 'none' }}
    >
      <span className="rail-resize-handle__grip" />
    </div>
  );
}
