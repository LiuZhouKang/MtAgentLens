'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { MotionPathPlugin } from 'gsap/MotionPathPlugin';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { FrameMask } from './frame-mask';
import { WordReveal } from './word-reveal';
import { landingCopy } from '@/lib/landing/landing-copy';
import { landingScroller } from '@/lib/landing/scroll';
import { usePauseOffscreen } from '@/lib/landing/pause-offscreen';
import { computeDemoLayout } from '@/lib/landing/demo-layout';

gsap.registerPlugin(ScrollTrigger, MotionPathPlugin);

const VB_W = 1000;
const VB_H = 800;

/** 每条边上的流动粒子数 —— 负 animation-delay 错相，形成连续粒子流 */
const PARTICLE_COUNT = 3;

function particleColor(edgeType: string): string {
  switch (edgeType) {
    case 'TaintPropagation':
      return '#fbbf24';
    case 'PolicyBoundaryCrossing':
      return '#8b7dff';
    default:
      return '#6cc2ff';
  }
}

/**
 * ④ Real demo — a living contamination-propagation responsibility graph.
 *
 * A pollution source (`/etc/shadow`) flows contamination through a working
 * chain (Writer → Builder → report) until a policy boundary crossing is
 * attributed. Rendered as a deterministic SVG graph (see `computeDemoLayout`):
 *
 *  1. AMBIENT LIFE — glowing particles flow along every edge (pure CSS
 *     `offset-path`, no JS, auto-respects reduced-motion); nodes breathe.
 *  2. CONTAMINATION NARRATIVE (scroll-scrubbed) — as the section scrolls, a
 *     contamination wave travels along the taint chain (MotionPathPlugin),
 *     the source / intermediate nodes turn amber as the wave reaches them,
 *     and finally the boundary crossing flashes with an AUTHORITATIVE verdict
 *     badge.
 *  3. HOVER — a node magnifies with a stronger glow.
 *
 * Reduced-motion / no-JS: the narrative and breathing are never applied — the
 * graph renders fully visible with only the CSS particle flow (which the
 * reduced-motion media query stops). No canvas anywhere.
 */
export function LandingLiveDemo() {
  const rootRef = useRef<HTMLElement>(null);
  // 离屏暂停：沿边粒子 / edge 流光 / 波纹的持续动画在离开视口时暂停
  usePauseOffscreen(rootRef);
  const layout = computeDemoLayout(VB_W, VB_H);

  // 污染传播链路径 —— taint 弧 + capability 弧 首尾拼接为「单条连续路径」。
  // MotionPathPlugin 对多 M 子路径解析不稳（报 progress undefined），
  // 故去掉后续子路径的 `M x y`，只保留首段的 M。
  const chainSegments = layout.edges.filter(
    (e) => e.edgeType === 'TaintPropagation' || e.edgeType === 'CapabilityUse',
  );
  const chainPath = chainSegments
    .map((e, i) => (i === 0 ? e.path : e.path.replace(/^M[^Q]*/, '')))
    .join(' ');
  const boundaryNode = layout.nodes.find((n) => n.kind === 'PolicyBoundary');

  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const q = gsap.utils.selector(root);
      const svg = root.querySelector('.landing-demo__svg');
      const chain = q('.demo-taint-chain');
      const verdict = q('.demo-verdict');
      if (!chainPath) return;

      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: root,
          start: 'top 85%',
          end: 'bottom 40%',
          scrub: true,
          scroller: landingScroller(),
          onUpdate: (st) => {
            // 污染过半 → 切换 is-contaminated：taint/write 边粒子增亮为琥珀。
            // 用 ScrollTrigger 的 progress（0–1 scrub 区间），不要用 timeline
            // 的 onUpdate —— 后者参数在某些版本不传 tween（报 progress undefined）。
            svg?.classList.toggle('is-contaminated', st.progress > 0.5);
          },
        },
      });

      tl.fromTo(q('.demo-node'), { opacity: 0.2 }, { opacity: 1, stagger: 0.06, duration: 0.2 }, 0)
        .fromTo(
          q('.demo-edge'),
          { opacity: 0.15 },
          { opacity: 0.8, stagger: 0.05, duration: 0.2 },
          0.05,
        )
        // 污染源点亮 —— 琥珀（读 /etc/shadow 的那一刻）。
        // 注意：必须 tween glyph（[data-glyph]）而非 g —— g 上的 stroke 会被
        // 子 glyph 自身的 stroke 属性覆盖。
        .to(
          q('.demo-node[data-node="res-shadow"] [data-glyph]'),
          { stroke: '#fbbf24', fillOpacity: 0.95, duration: 0.2 },
          0.3,
        )
        // 污染波沿链推进 —— MotionPath 由 scrub 驱动（"看见污染正在流动"）
        .fromTo(
          chain,
          { opacity: 0, motionPath: { path: chainPath, start: 0 } },
          { opacity: 0.95, motionPath: { path: chainPath, end: 1 }, duration: 1.1, ease: 'power1.inOut' },
          0.4,
        )
        // 波前经过 → 节点逐一点燃污染
        .to(
          q('.demo-node[data-node="agent-builder"] [data-glyph]'),
          { stroke: '#fbbf24', fillOpacity: 0.92, duration: 0.18 },
          0.78,
        )
        .to(
          q('.demo-node[data-node="res-report"] [data-glyph]'),
          { stroke: '#fbbf24', fillOpacity: 0.85, duration: 0.18 },
          1.08,
        )
        // 边界穿越 → 判定
        .to(
          q('.demo-edge[data-edge="PolicyBoundaryCrossing"]'),
          { stroke: '#8b7dff', opacity: 0.95, strokeWidth: 2.5, duration: 0.2 },
          1.35,
        )
        .to(
          q('.demo-node[data-node="boundary-system"] [data-glyph]'),
          { stroke: '#6cc2ff', fillOpacity: 0.95, duration: 0.18 },
          1.55,
        )
        .fromTo(
          verdict,
          { opacity: 0, scale: 0.6 },
          { opacity: 1, scale: 1, duration: 0.3, ease: 'back.out(1.7)' },
          1.72,
        );

      return () => {
        tl.scrollTrigger?.kill();
        tl.kill();
      };
    },
    { scope: rootRef },
  );

  return (
    <section
      id="demo"
      ref={rootRef}
      className="landing-section landing-demo anim-inview"
      aria-labelledby="demo-title"
    >
      <FrameMask>
        <div className="landing-section__head">
          <WordReveal as="h2" id="demo-title" text={landingCopy.demo.heading} />
          <p className="landing-section__sub">{landingCopy.demo.subheading}</p>
        </div>

        <div className="landing-demo__window" data-testid="demo-window">
          {/* 控制台窗口标题栏 —— 让活图读起来是真实产品截图 */}
          <div className="landing-demo__windowbar" aria-hidden="true">
            <span className="landing-demo__dots">
              <i />
              <i />
              <i />
            </span>
            <span className="landing-demo__title">RESPONSIBILITY GRAPH</span>
            <span className="landing-demo__live">
              <i />
              LIVE
            </span>
          </div>

          <figure className="landing-demo__stage" data-testid="demo-graph">
          <svg
            viewBox={`0 0 ${VB_W} ${VB_H}`}
            className="landing-demo__svg"
            role="img"
            aria-label="责任图演示：污染源传播到归因判定"
          >
            {/* edges + 沿边流动粒子（CSS offset-path） */}
            {layout.edges.map((e) => (
              <g key={`${e.source}-${e.target}`}>
                <path
                  d={e.path}
                  className="demo-edge"
                  data-edge={e.edgeType}
                  fill="none"
                  stroke="#6cc2ff"
                  strokeWidth="1.5"
                  opacity="0.55"
                />
                {Array.from({ length: PARTICLE_COUNT }, (_, i) => (
                  <circle
                    key={`p${i}`}
                    className="demo-particle"
                    data-edge={e.edgeType}
                    // cx/cy 必须显式 0：offset-path 以元素自身 box 为锚，
                    // 默认 offset-anchor:auto 才能让圆心精确落在路径上
                    cx="0"
                    cy="0"
                    r={i === 0 ? 2.4 : 1.9}
                    fill={particleColor(e.edgeType)}
                    style={{
                      offsetPath: `path('${e.path}')`,
                      offsetRotate: '0deg',
                      animationDelay: `-${i * 1.15}s`,
                      animationDuration: `${2.7 + (i % PARTICLE_COUNT) * 0.45}s`,
                    }}
                  />
                ))}
              </g>
            ))}

            {/* 污染传播波前 —— 位置/进度全由 GSAP MotionPath 驱动 */}
            <circle className="demo-taint-chain" r="6.5" fill="none" stroke="#fbbf24" strokeWidth="2" opacity="0" />

            {/* nodes */}
            {layout.nodes.map((n) => {
              const isSource = n.id === 'res-shadow';
              const isAttribution = n.id === 'boundary-system';
              const cls = [
                'demo-node',
                isSource && 'demo-node--source',
                isAttribution && 'demo-node--attribution',
              ]
                .filter(Boolean)
                .join(' ');
              return (
                <g key={n.id} className={cls} data-node={n.id} opacity="0.9">
                  <NodeGlyph node={n} />
                  {/* 污染源 —— 持续向外扩散的琥珀波纹（污染正在传播） */}
                  {isSource && (
                    <circle
                      cx={n.x}
                      cy={n.y}
                      r={12}
                      className="demo-ripple"
                      fill="none"
                      stroke="#fbbf24"
                    />
                  )}
                  <text
                    x={n.x + 18}
                    y={n.y + 4}
                    className="landing-demo__label"
                    fill="currentColor"
                  >
                    {n.label}
                  </text>
                </g>
              );
            })}

            {/* 归因判定徽章 —— 边界穿越后出现的 AUTHORITATIVE 裁决 */}
            {boundaryNode && (
              <g
                className="demo-verdict"
                transform={`translate(${boundaryNode.x}, ${boundaryNode.y + 34})`}
                opacity="0"
              >
                <rect x="-74" y="-17" width="148" height="34" rx="17" className="demo-verdict__pill" />
                <text x="0" y="3" textAnchor="middle" className="demo-verdict__text">
                  判定 · AUTHORITATIVE
                </text>
              </g>
            )}
          </svg>
          </figure>
        </div>

        <ul className="landing-demo__capabilities" aria-label="能力标签">
          {landingCopy.demo.capabilities.map((cap) => (
            <li key={cap} className="landing-demo__cap">
              {cap}
            </li>
          ))}
        </ul>
      </FrameMask>
    </section>
  );
}

function NodeGlyph({ node }: { node: { id: string; kind: string; x: number; y: number } }) {
  // data-glyph：污染叙事直接 tween glyph 元素（g 上的 stroke 会被子 glyph
  // 自己的 stroke 属性覆盖，必须 tween 到具体图形）。
  const common = {
    'data-glyph': true,
    stroke: '#6cc2ff',
    strokeWidth: 2,
    fill: '#6cc2ff',
    fillOpacity: 0.22,
  } as const;
  switch (node.kind) {
    case 'Agent':
      return <circle cx={node.x} cy={node.y} r={10} {...common} />;
    case 'PolicyBoundary':
      return (
        <path
          d={`M${node.x} ${node.y - 13} L${node.x + 13} ${node.y} L${node.x} ${node.y + 13} L${node.x - 13} ${node.y} Z`}
          {...common}
          fill="#8b7dff"
          fillOpacity={0.22}
        />
      );
    default:
      return (
        <rect
          x={node.x - 11}
          y={node.y - 11}
          width={22}
          height={22}
          rx={4}
          {...common}
        />
      );
  }
}
