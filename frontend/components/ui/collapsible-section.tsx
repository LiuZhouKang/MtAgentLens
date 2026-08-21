'use client';

import { useRef, useState, type ReactNode } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ChevronDown, ChevronRight } from 'lucide-react';

/**
 * 折叠分节——仪表盘深链视图的分节容器。
 *
 * 头部（eyebrow + title + meta + chevron）点击展开/收起内容；`aria-expanded`
 * 表达状态，Enter/Space 均可激活（WAI-ARIA 按钮约定）。默认收起，首屏保持干净。
 *
 * 展开/收起用 GSAP 高度过渡（顶级流畅，无跳动）；reduced-motion 直接切换。
 * 默认收起时不渲染内容（首屏干净 + 无文本匹配），首次展开后常驻 DOM（可再次收起）。
 */
export function CollapsibleSection({
  eyebrow,
  title,
  meta,
  defaultOpen = false,
  children,
}: {
  eyebrow: string;
  title: string;
  meta?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  // 首次展开后常驻 DOM（收起仅隐藏，保留测量高度供二次展开动画）
  const [wasOpened, setWasOpened] = useState(defaultOpen);
  const bodyRef = useRef<HTMLDivElement>(null);

  // 展开/收起高度过渡 —— 只动 height + opacity（内容在 DOM，过渡 280ms）
  useGSAP(
    () => {
      const body = bodyRef.current;
      if (!body) return;
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduce) {
        gsap.set(body, { height: open ? 'auto' : 0, visibility: open ? 'visible' : 'hidden' });
        return;
      }
      gsap.to(body, {
        height: open ? 'auto' : 0,
        opacity: open ? 1 : 0,
        duration: 0.28,
        ease: 'power2.inOut',
        overwrite: 'auto',
        onStart: () => {
          if (open) gsap.set(body, { visibility: 'visible' });
        },
        onComplete: () => {
          if (!open) gsap.set(body, { visibility: 'hidden' });
        },
      });
    },
    { scope: bodyRef, dependencies: [open] },
  );

  const toggle = () => {
    setOpen((v) => !v);
    setWasOpened(true);
  };

  return (
    <section
      className="ln-panel"
      data-open={open ? 'true' : 'false'}
      style={{ overflow: 'hidden' }}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === ' ' || e.key === 'Spacebar') {
            e.preventDefault();
            toggle();
          }
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          width: '100%',
          textAlign: 'left',
          padding: '12px 16px',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--ln-text)',
        }}
      >
        <span
          aria-hidden
          style={{
            display: 'flex',
            alignItems: 'center',
            color: 'var(--ln-text-secondary)',
            transition: 'transform 160ms ease',
            transform: open ? 'rotate(0deg)' : 'rotate(-90deg)',
            flexShrink: 0,
          }}
        >
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </span>
        <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
          <span className="ln-eyebrow">{eyebrow}</span>
          <strong style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-0.01em' }}>
            {title}
          </strong>
        </span>
        {meta && (
          <span className="ln-panel__meta" style={{ marginLeft: 'auto', flexShrink: 0 }}>
            {meta}
          </span>
        )}
      </button>
      {wasOpened && (
        <div
          ref={bodyRef}
          aria-hidden={!open}
          style={{ height: open ? 'auto' : 0, overflow: 'hidden' }}
        >
          <div className="ln-panel-body">{children}</div>
        </div>
      )}
    </section>
  );
}
