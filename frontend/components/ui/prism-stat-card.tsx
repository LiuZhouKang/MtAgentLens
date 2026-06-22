'use client';

import type { CSSProperties, HTMLAttributes, ReactNode } from 'react';

type PrismStatCardProps = HTMLAttributes<HTMLDivElement> & {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  dense?: boolean;
  toneColor?: string;
};

export default function PrismStatCard({
  label,
  value,
  hint,
  dense = false,
  toneColor,
  style,
  ...props
}: PrismStatCardProps) {
  const nextStyle: CSSProperties = {
    borderColor: toneColor ? `${toneColor}26` : undefined,
    background: toneColor
      ? `linear-gradient(180deg, ${toneColor}0f 0%, rgba(255,255,255,0.024) 100%)`
      : undefined,
    padding: dense ? '8px 10px' : undefined,
    ...style,
  };

  return (
    <div className="prism-kpi" style={nextStyle} {...props}>
      <div className="prism-kpi__label">{label}</div>
      <div className="prism-kpi__value" style={dense ? { fontSize: 14 } : undefined}>
        {value}
      </div>
      {hint ? <div className="prism-kpi__hint">{hint}</div> : null}
    </div>
  );
}
