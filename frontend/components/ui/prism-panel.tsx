'use client';

import type { HTMLAttributes, ReactNode } from 'react';

type PrismPanelSize = 'sm' | 'md' | 'lg' | 'hero';

type PrismPanelProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode;
  size?: PrismPanelSize;
  grid?: boolean;
  shimmer?: boolean;
};

export default function PrismPanel({
  children,
  className,
  size = 'md',
  grid = false,
  shimmer = false,
  ...props
}: PrismPanelProps) {
  return (
    <div
      className={[
        'prism-surface',
        `prism-surface--${size}`,
        grid ? 'prism-grid-surface' : '',
        shimmer ? 'prism-shimmer-border' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      {...props}
    >
      {children}
    </div>
  );
}
