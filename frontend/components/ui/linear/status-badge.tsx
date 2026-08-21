'use client';

import type { ReactNode } from 'react';

export type StatusTone = 'success' | 'danger' | 'warning' | 'info' | 'muted';

type StatusBadgeProps = {
  tone?: StatusTone;
  children: ReactNode;
};

export function StatusBadge({ tone = 'muted', children }: StatusBadgeProps) {
  return (
    <span className="ln-badge" data-tone={tone}>
      {children}
    </span>
  );
}
