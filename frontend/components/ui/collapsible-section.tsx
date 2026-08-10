'use client';

import { useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

/**
 * 折叠分节——仪表盘深链视图的分节容器。
 *
 * 头部（eyebrow + title + meta + chevron）点击展开/收起内容；`aria-expanded`
 * 表达状态，Enter/Space 均可激活（WAI-ARIA 按钮约定）。默认收起，首屏保持干净。
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

  const toggle = () => setOpen((v) => !v);

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
      {open && <div className="ln-panel-body">{children}</div>}
    </section>
  );
}
