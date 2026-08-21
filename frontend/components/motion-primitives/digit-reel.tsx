'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * DigitReel —— 顶级数字计数器（Stripe/Linear 翻牌机语言）。
 *
 * 数字按千分位拆位，每个数字位是竖排 0-9 的窄槽，每位独立 CSS animation
 * 从下方滚入目标值——老式翻牌机/计分牌效果。逗号与负号是静态位。
 *
 * - SSR / reduced-motion：直接渲染终值（无动画、无闪烁）。
 * - 首次挂载：不重触发动画（外层卡片 blur 入场已足够，避免叠加杂乱）。
 * - 数值更新：value 变化时 key 变化重跑 animation（从 +1em 滚到 --roll-target）。
 * - 纯 CSS transform animation —— 无 JS 引用，天然无 removeChild 竞态。
 * - 只动 transform（GPU 合成），符合 gsap-performance 规范。
 */
export function DigitReel({
  value,
  className,
  stagger = 0.03,
}: {
  value: number;
  className?: string;
  stagger?: number;
}) {
  // 千分位拆位："1316" → ["1",",","3","1","6"]；"-42" → ["-","4","2"]
  const parts = value
    .toLocaleString('en-US')
    .split('')
    .map((ch) => ({ ch, digit: /[0-9]/.test(ch) ? Number(ch) : null }));

  // 只对「数值更新」重触发翻牌动画：首次挂载保持静止（避免与卡片入场叠加）。
  const [animId, setAnimId] = useState(0);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setAnimId((k) => k + 1);
  }, [value]);

  const animate = animId > 0;

  return (
    <span className={className} aria-label={String(value)}>
      {parts.map((p, i) =>
        p.digit != null ? (
          <span
            key={`${animId}-d${i}`}
            aria-hidden
            style={{
              display: 'inline-block',
              overflow: 'hidden',
              verticalAlign: 'bottom',
              height: '1em',
              lineHeight: 1,
            }}
          >
            <span
              data-reel-roll
              style={
                {
                  display: 'flex',
                  flexDirection: 'column',
                  transform: `translateY(${p.digit * -1}em)`,
                  '--roll-target': `${p.digit * -1}em`,
                  ...(animate
                    ? {
                        animation: `digit-reel-roll 0.7s cubic-bezier(0.22, 1, 0.36, 1) ${i * stagger}s both`,
                      }
                    : {}),
                  willChange: 'transform',
                } as React.CSSProperties
              }
            >
              {Array.from({ length: 10 }, (_, d) => (
                <span key={d} style={{ display: 'block', height: '1em' }}>
                  {d}
                </span>
              ))}
            </span>
          </span>
        ) : (
          <span key={`${animId}-c${i}`} aria-hidden>
            {p.ch}
          </span>
        ),
      )}
    </span>
  );
}
