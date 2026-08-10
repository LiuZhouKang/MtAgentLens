'use client';

import { useId, useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

/**
 * MtAgentLens brand mark — "Observation Orbit" (v7.2, seamless living).
 *
 * Minimal Atom-essence structure, amber-fire palette #fbbf24 → #f97316.
 *
 * Seamless living layer — ONE master timeline, unified 2.2s period, so every
 * animation shares a beat (no drifting phases → no visible "stutter"):
 *  1. ORBIT DATA FLOW — dashes march (period-synced).
 *  2. RADAR PULSE ×2 — each ring does fade-in → expand → fade-out; the loop
 *     joins at opacity 0 on both ends, so pulses never hard-cut (the "skip"
 *     you saw came from opacity jumping 0.6 → 0 at the loop point).
 *  3. CORE BREATH + GLOW — sine yoyo, same 2.2s period, in phase.
 *  4. HOVER — independent quiet lift (never interrupts the master timeline).
 * All GPU-composited (transform + opacity). Respects reduced-motion.
 */
export function MtlLogo({
  size = 20,
  className,
  animated = true,
  mono = false,
}: {
  size?: number;
  className?: string;
  animated?: boolean;
  /** Static monochrome line style — inherits currentColor, matches icon/button aesthetics */
  mono?: boolean;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  // 每实例唯一的渐变 ID —— 防跨实例 url(#id) 解析到被 display:none 的兄弟渐变
  // （SVG 规范：引用不渲染子树里的 paint server 会失效，导致核心/轨道不绘制）
  const gradId = `mtl-grad-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;

  useGSAP(
    () => {
      const svg = svgRef.current;
      if (!animated || !svg) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const q = gsap.utils.selector(svg);
      const ORIGIN = '256 256';
      const T = 2.2; // unified period (s)
      const fadeIn = 0.25; // pulse fade-in (s)

      // ── 1. Orbit data flow — one continuous march, period-synced ──
      gsap.fromTo(
        q('.mtl-orbit'),
        { strokeDashoffset: 0 },
        { strokeDashoffset: -924, duration: T, ease: 'none', repeat: -1 },
      );

      // ── 2. Master timeline — radar pulses, seamless loop (opacity 0 at both ends) ──
      const tl = gsap.timeline({ repeat: -1 });
      // Pulse 1 (first half of the period)
      tl.fromTo(
        q('.mtl-pulse'),
        { scale: 0.85, opacity: 0 },
        { scale: 0.85, opacity: 0.55, duration: fadeIn, ease: 'power1.in', svgOrigin: ORIGIN },
        0,
      )
        .to(
          q('.mtl-pulse'),
          { scale: 1.75, opacity: 0, duration: T / 2 - fadeIn, ease: 'power1.out', svgOrigin: ORIGIN },
          fadeIn,
        );
      // Pulse 2 (second half — exactly staggered)
      tl.fromTo(
        q('.mtl-pulse2'),
        { scale: 0.85, opacity: 0 },
        { scale: 0.85, opacity: 0.45, duration: fadeIn, ease: 'power1.in', svgOrigin: ORIGIN },
        T / 2,
      )
        .to(
          q('.mtl-pulse2'),
          { scale: 1.75, opacity: 0, duration: T / 2 - fadeIn, ease: 'power1.out', svgOrigin: ORIGIN },
          T / 2 + fadeIn,
        );

      // ── 3. Core breathes + glow, same 2.2s period, in phase ──
      gsap.to(q('.mtl-core'), {
        scale: 1.09,
        duration: T,
        yoyo: true,
        repeat: -1,
        ease: 'sine.inOut',
        svgOrigin: ORIGIN,
      });
      gsap.to(q('.mtl-glow'), {
        opacity: 0.42,
        duration: T,
        yoyo: true,
        repeat: -1,
        ease: 'sine.inOut',
      });

      // ── 4. Hover — independent quiet lift (never touches the master timeline) ──
      const enter = () =>
        gsap.to(svg, { scale: 1.05, duration: 0.35, ease: 'power2.out', overwrite: true });
      const leave = () =>
        gsap.to(svg, { scale: 1, duration: 0.45, ease: 'power2.out', overwrite: true });
      svg.addEventListener('pointerenter', enter);
      svg.addEventListener('pointerleave', leave);

      // Pause when tab hidden
      const onVis = () =>
        document.hidden ? gsap.globalTimeline.pause() : gsap.globalTimeline.resume();
      document.addEventListener('visibilitychange', onVis);

      return () => {
        svg.removeEventListener('pointerenter', enter);
        svg.removeEventListener('pointerleave', leave);
        document.removeEventListener('visibilitychange', onVis);
      };
    },
    { dependencies: [animated], scope: svgRef },
  );

  // ── Mono mode: static, single-color linework (dock button / icon style) ──
  if (mono) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 512 512"
        fill="none"
        className={className}
        aria-label="MtAgentLens"
        role="img"
        style={{ display: 'block', color: 'currentColor' }}
      >
        <ellipse
          cx="256"
          cy="256"
          rx="206"
          ry="72"
          stroke="currentColor"
          fill="none"
          strokeLinecap="round"
          strokeDasharray="868 62"
          style={{ strokeWidth: 38 }}
          opacity="0.9"
          transform="rotate(6 256 256)"
        />
        <ellipse
          cx="256"
          cy="256"
          rx="178"
          ry="62"
          stroke="currentColor"
          fill="none"
          strokeLinecap="round"
          strokeDasharray="620 220"
          style={{ strokeWidth: 32 }}
          opacity="0.55"
          transform="rotate(-55 256 256)"
        />
        <ellipse
          cx="256"
          cy="256"
          rx="140"
          ry="48"
          stroke="currentColor"
          fill="none"
          strokeLinecap="round"
          strokeDasharray="340 330"
          style={{ strokeWidth: 26 }}
          opacity="0.28"
          transform="rotate(65 256 256)"
        />
        {/* Core — open ring, line style (matches button icons) */}
        <circle
          cx="256"
          cy="256"
          r="46"
          stroke="currentColor"
          fill="none"
          strokeLinecap="round"
          style={{ strokeWidth: 30 }}
        />
      </svg>
    );
  }

  return (
    <svg
      ref={svgRef}
      width={size}
      height={size}
      viewBox="0 0 512 512"
      fill="none"
      className={className}
      aria-label="MtAgentLens"
      role="img"
      style={{ display: 'block', transformBox: 'fill-box', transformOrigin: 'center' }}
    >
      <defs>
        <linearGradient
          id={gradId}
          x1="80"
          y1="80"
          x2="432"
          y2="432"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#fbbf24" />
          <stop offset="1" stopColor="#f97316" />
        </linearGradient>
      </defs>

      {/* Ambient glow — breathes in sync with the core */}
      <circle className="mtl-glow" cx="256" cy="256" r="54" fill="#fbbf24" opacity="0.22" />

      {/* Radar pulse rings — two seamless staggered scans */}
      <circle
        className="mtl-pulse"
        cx="256"
        cy="256"
        r="54"
        stroke="#fbbf24"
        strokeWidth="3"
        fill="none"
        opacity="0"
      />
      <circle
        className="mtl-pulse2"
        cx="256"
        cy="256"
        r="54"
        stroke="#f97316"
        strokeWidth="2.5"
        fill="none"
        opacity="0"
      />

      {/* Orbit A — large */}
      <ellipse
        className="mtl-orbit"
        cx="256"
        cy="256"
        rx="206"
        ry="72"
        stroke={`url(#${gradId})`}
        strokeWidth="14"
        strokeLinecap="round"
        strokeDasharray="868 62"
        opacity="0.9"
        transform="rotate(6 256 256)"
      />
      {/* Orbit B — medium */}
      <ellipse
        className="mtl-orbit"
        cx="256"
        cy="256"
        rx="178"
        ry="62"
        stroke={`url(#${gradId})`}
        strokeWidth="12"
        strokeLinecap="round"
        strokeDasharray="700 140"
        opacity="0.55"
        transform="rotate(-55 256 256)"
      />
      {/* Orbit C — small */}
      <ellipse
        className="mtl-orbit"
        cx="256"
        cy="256"
        rx="140"
        ry="48"
        stroke={`url(#${gradId})`}
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray="450 220"
        opacity="0.28"
        transform="rotate(65 256 256)"
      />

      {/* Agent core — one clean gradient body */}
      <g className="mtl-core">
        <circle cx="256" cy="256" r="46" fill={`url(#${gradId})`} />
      </g>

      {/* Quiet glass highlight */}
      <circle cx="240" cy="240" r="9" fill="#ffffff" opacity="0.5" />
    </svg>
  );
}

export default MtlLogo;
