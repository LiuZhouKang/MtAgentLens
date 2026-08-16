'use client';

import { useId, useRef, useState, type MouseEvent } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

/**
 * 交互式无轴 sparkline —— 顶级监控卡的真实迷你趋势。
 *
 * 静态：与 KPI 卡趋势同语言（趋势线 + 渐变填充，无坐标轴）。
 * 入场：GSAP 沿路径绘制（getTotalLength 精确取长 → dashoffset 动画到 0），
 *       线条从左画到右。reduced-motion 直接显示。
 * 交互：鼠标放在线条上出现一个小圆点（白芯 + 状态色光晕）沿曲线平滑跟随，
 *       并带垂直参考线 + 顶部 mono 值浮层。圆点经线性插值在数据点间连续滑动。
 *
 * 数据由真实时间序列派生（derivePerspectiveMetric.trend），有 ≥2 点才渲染。
 */
export function Sparkline({
  points,
  color,
  height = 32,
  formatValue,
  animate = true,
}: {
  points: Array<{ name: string | number; value: number }>;
  color: string;
  height?: number;
  formatValue?: (value: number) => string;
  /** 入场绘制动画开关（卡片重排/数据更新时仍绘制；reduced-motion 自动关闭） */
  animate?: boolean;
}) {
  const gid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const wrapRef = useRef<HTMLSpanElement>(null);
  const lineRef = useRef<SVGPolylineElement>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);

  // GSAP 路径绘制：getTotalLength() 取真实长度（viewBox 坐标），dashoffset 从全长画到 0。
  // 不用 pathLength=1 + vector-effect 组合（在 preserveAspectRatio="none" 下会失真/线条隐藏）。
  // 绘制完成后 clearProps 清掉 dash 属性（防 GSAP 残留引用导致卸载竞态 removeChild）。
  useGSAP(
    () => {
      const line = lineRef.current;
      if (!line) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const len = line.getTotalLength();
      const tween = gsap.fromTo(
        line,
        { strokeDasharray: len, strokeDashoffset: len },
        {
          strokeDashoffset: 0,
          duration: 0.7,
          ease: 'power2.inOut',
          delay: 0.3,
          onComplete: () => {
            if (line.isConnected) {
              gsap.set(line, { clearProps: 'strokeDasharray, strokeDashoffset' });
            }
          },
        },
      );
      // useGSAP 自动在卸载时 kill；此处双保险
      return () => tween.kill();
    },
    { scope: wrapRef, dependencies: [points, animate] },
  );

  if (points.length < 2) return null;

  const W = 120;
  const H = height;
  const PAD = 4;
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const pts = points.map((p, i) => {
    const x = PAD + (i / (points.length - 1)) * (W - PAD * 2);
    const y = H - PAD - ((p.value - min) / range) * (H - PAD * 2);
    return { x, y };
  });
  const polyline = pts.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');

  // hoverX → 沿线平滑跟随：在两数据点间线性插值 y，取最近点作为浮层值
  let hoverY = 0;
  let hoverValue: number | null = null;
  if (hoverX != null) {
    const clamped = Math.max(pts[0]!.x, Math.min(pts[pts.length - 1]!.x, hoverX));
    for (let i = 0; i < pts.length - 1; i += 1) {
      const a = pts[i]!;
      const b = pts[i + 1]!;
      if (clamped >= a.x && clamped <= b.x) {
        const t = (clamped - a.x) / (b.x - a.x || 1);
        hoverY = a.y + t * (b.y - a.y);
        const near = t < 0.5 ? i : i + 1;
        hoverValue = points[near]!.value;
        break;
      }
    }
  }
  const hasHover = hoverX != null;

  const onMouseMove = (e: MouseEvent<HTMLSpanElement>) => {
    const el = wrapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const rel = (e.clientX - rect.left) / rect.width;
    setHoverX(rel * W);
  };

  return (
    <span
      ref={wrapRef}
      onMouseMove={onMouseMove}
      onMouseLeave={() => setHoverX(null)}
      style={{ position: 'relative', display: 'block', width: '100%', height }}
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        style={{ display: 'block', width: '100%', height }}
      >
        <defs>
          <linearGradient id={`spark-${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.2} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
          <radialGradient id={`spark-glow-${gid}`}>
            <stop offset="0%" stopColor={color} stopOpacity={0.9} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </radialGradient>
        </defs>
        <polygon
          points={`${PAD},${H - PAD} ${polyline} ${W - PAD},${H - PAD}`}
          fill={`url(#spark-${gid})`}
        />
        <polyline
          ref={lineRef}
          points={polyline}
          fill="none"
          stroke={color}
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/* 小圆点：光晕 + 白芯 + 状态色描边 —— 沿曲线平滑跟随鼠标 */}
        {hasHover ? (
          <>
            <circle cx={hoverX} cy={hoverY} r={7} fill={`url(#spark-glow-${gid})`} />
            <circle
              cx={hoverX}
              cy={hoverY}
              r={3.2}
              fill={color}
              stroke="#fff"
              strokeWidth={1.4}
            />
          </>
        ) : null}
      </svg>

      {/* hover 值浮层 —— 垂直参考线 + 顶部 mono 值 */}
      {hasHover ? (
        <span
          aria-hidden
          style={{
            position: 'absolute',
            top: 0,
            left: `${(hoverX / W) * 100}%`,
            transform: 'translateX(-50%)',
            pointerEvents: 'none',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 2,
          }}
        >
          <span
            style={{
              fontFamily: 'var(--ln-mono, monospace)',
              fontSize: 10,
              lineHeight: 1,
              color: 'var(--ln-text)',
              background: 'var(--ln-surface-3, #1f2021)',
              border: '1px solid var(--ln-border-strong, rgba(255,255,255,0.14))',
              borderRadius: 4,
              padding: '3px 6px',
              whiteSpace: 'nowrap',
            }}
          >
            {formatValue ? formatValue(hoverValue ?? 0) : hoverValue}
          </span>
          <span
            style={{
              width: 1,
              height: H,
              background: 'var(--ln-border-strong, rgba(255,255,255,0.18))',
            }}
          />
        </span>
      ) : null}
    </span>
  );
}
