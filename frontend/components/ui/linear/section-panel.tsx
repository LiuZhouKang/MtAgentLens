'use client';

import type { ReactNode } from 'react';

type SectionPanelProps = {
  title?: string;
  eyebrow?: string;
  meta?: string | ReactNode;
  children: ReactNode;
  className?: string;
  hoverable?: boolean;
  bodyClassName?: string;
};

export function SectionPanel({
  title,
  eyebrow,
  meta,
  children,
  className = '',
  hoverable,
  bodyClassName = '',
}: SectionPanelProps) {
  return (
    <section className={`ln-panel ${hoverable ? 'ln-panel--hoverable' : ''} ${className}`.trim()}>
      {(title || eyebrow || meta) && (
        <header className="ln-panel-head">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {eyebrow ? <span className="ln-eyebrow">{eyebrow}</span> : null}
            {title ? <h3>{title}</h3> : null}
          </div>
          {meta ? <span className="ln-panel__meta">{meta}</span> : null}
        </header>
      )}
      <div className={`ln-panel-body ${bodyClassName}`.trim()}>{children}</div>
    </section>
  );
}
