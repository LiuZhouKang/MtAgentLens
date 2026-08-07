'use client';

import { useId } from 'react';
import { Area, AreaChart, ResponsiveContainer } from 'recharts';
import type { StatusTone } from './status-badge';

type MetricCardProps = {
  label: string;
  value: string | number;
  delta?: string;
  deltaTone?: StatusTone;
  spark?: number[];
  className?: string;
};

export function MetricCard({
  label,
  value,
  delta,
  deltaTone = 'info',
  spark,
  className = '',
}: MetricCardProps) {
  const gid = useId();
  const data = spark ? spark.map((v, i) => ({ i, v })) : [];

  return (
    <div className={`ln-panel ln-metric ${className}`.trim()}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <span className="ln-metric__label">{label}</span>
        {spark && spark.length > 1 ? (
          <div style={{ width: 64, height: 22 }} aria-hidden="true">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#5e6ad2" stopOpacity={0.45} />
                    <stop offset="100%" stopColor="#5e6ad2" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Area
                  type="monotone"
                  dataKey="v"
                  stroke="#5e6ad2"
                  strokeWidth={1.5}
                  fill={`url(#${gid})`}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </div>
      <strong className="ln-metric__value">{value}</strong>
      {delta ? (
        <span className="ln-metric__delta" data-tone={deltaTone}>
          {delta}
        </span>
      ) : null}
    </div>
  );
}
