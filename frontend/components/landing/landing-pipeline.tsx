'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { BentoTilt } from './benti-tilt';
import { FrameMask } from './frame-mask';
import { WordReveal } from './word-reveal';
import { landingCopy } from '@/lib/landing/landing-copy';
import { landingScroller } from '@/lib/landing/scroll';
import { LANDING_REVEAL } from '@/lib/landing/reveal';
import { usePauseOffscreen } from '@/lib/landing/pause-offscreen';

gsap.registerPlugin(ScrollTrigger);

/** 真实控制台内容（装饰，aria-hidden）——让管线读起来像产品截图而非抽象卡片 */
const CONSOLE_LINES: Record<string, string[]> = {
  ebpf: [
    '$ ./mtagentlens record -c claude',
    '[tls_capture] 1.2 KB plaintext',
    '[tls_capture] claude → api.openai.com',
    '[process] exec builder-agent · pid 8120',
    '[stdiocap] MCP stdio · 812 B',
  ],
  rust: [
    'id: agent-writer → agent-builder',
    'taint: /etc/shadow 已追踪',
    'identity: root_pid + tls + session 合并',
  ],
  observe: [
    'alert: 污染源判定 · /etc/shadow',
    'evidence: 3 条 · AUTHORITATIVE',
    'traceback: 4 跳可回溯',
  ],
};

const STATUS: Record<string, string> = {
  ebpf: 'REC',
  rust: 'LIVE',
  observe: 'OK',
};

/**
 * ③ The pipeline — three real console windows, one upward data flow.
 *
 * Each layer is rendered as a window from the actual product — a live probe
 * terminal (eBPF), an attribution graph panel (Rust), an alert/evidence
 * dashboard (Observe) — instead of abstract icon cards. Luminous data streams
 * carry the story upward between them (Observe on top, kernel at the base).
 *
 * Hovering tilts a window in 3D (BentoTilt) with a cursor spotlight; scroll
 * assembles the pipeline from the base up.
 */
export function LandingPipeline() {
  const { layers } = landingCopy.pipeline;
  const sectionRef = useRef<HTMLElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  // 离屏暂停：点阵背景/数据流的持续动画在 section 离开视口时暂停（顶级性能规范）
  usePauseOffscreen(sectionRef);
  // 纵向：Observe 在顶、eBPF 在底（数据自下而上流经管线）
  const ordered = [...layers].reverse();

  useGSAP(
    () => {
      const track = trackRef.current;
      if (!track) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      const q = gsap.utils.selector(track);
      // 从底部（内核）向上组装 —— 数据流自下而上点亮各层。
      // 注意：只做 y+opacity 干净上升，不做 rotationX/perspective 3D 翻转——
      // rotateX 的 from-state 会把节点投影加宽（391→381px+）在窄屏撑出横向溢出，
      // 且 3D 翻转并不比上升更优雅。
      const nodes = track.querySelectorAll<HTMLElement>('.landing-pipeline__node');
      // 节点随滚动 scrub 渐入（从内核向上点亮，跟随滚动）
      gsap.fromTo(
        nodes,
        { opacity: 0, y: 48 },
        {
          opacity: 1,
          y: 0,
          ease: 'none',
          stagger: 0.16,
          scrollTrigger: {
            trigger: track,
            start: LANDING_REVEAL.start,
            end: LANDING_REVEAL.end,
            scrub: LANDING_REVEAL.scrub,
            scroller: landingScroller(),
          },
        },
      );

      // 各窗口内部内容 —— 终端行级联 / 图节点依次点亮 / 观察卡片淡入（scrub）
      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: track,
          start: LANDING_REVEAL.start,
          end: LANDING_REVEAL.end,
          scrub: LANDING_REVEAL.scrub,
          scroller: landingScroller(),
        },
      });
      tl.fromTo(q('.landing-pipeline__line'), { opacity: 0, x: -6 }, { opacity: 1, x: 0, stagger: 0.12, ease: 'power2.out' }, 0)
        .fromTo(q('.landing-pipeline__graph-node'), { opacity: 0.15 }, { opacity: 1, stagger: 0.18, ease: 'power2.out' }, 0.2)
        .fromTo(q('.landing-pipeline__observe-card'), { opacity: 0, y: 10 }, { opacity: 1, y: 0, stagger: 0.12, ease: 'power2.out' }, 0.4);

      // 滚动视差：管线数据流整体随滚动穿过轻微上漂（深度感，顶级滚动体验）。
      // 独立于节点一次性 reveal（父容器 transform 与子元素 reveal 合成互不覆盖）。
      const parallax = gsap.to(track, {
        yPercent: -4,
        ease: 'none',
        scrollTrigger: {
          trigger: track,
          start: 'top bottom',
          end: 'bottom top',
          scrub: 0.5,
          scroller: landingScroller(),
        },
      });

      return () => {
        tl.scrollTrigger?.kill();
        tl.kill();
        parallax.scrollTrigger?.kill();
        parallax.kill();
      };
    },
    { scope: trackRef },
  );

  return (
    <section
      id="pipeline"
      ref={sectionRef}
      className="landing-section landing-pipeline anim-inview"
      aria-labelledby="pipeline-title"
    >
      <FrameMask>
        <div className="landing-section__head">
          <WordReveal as="h2" id="pipeline-title" text={landingCopy.pipeline.heading} />
          <p className="landing-section__sub">{landingCopy.pipeline.subheading}</p>
        </div>

        <div ref={trackRef} className="landing-pipeline__track">
          {ordered.map((layer, i) => (
            <div key={layer.id} className="landing-pipeline__layer">
              <BentoTilt
                className="landing-pipeline__node"
                data-testid={`pipeline-card-${layer.id}`}
              >
                {/* 窗口标题栏 */}
                <div className="landing-pipeline__windowbar">
                  <span className="landing-pipeline__dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                  <h3 className="landing-pipeline__name">{layer.name}</h3>
                  <span className="landing-pipeline__status" aria-hidden="true">
                    {STATUS[layer.id]}
                  </span>
                </div>

                {/* 真实产品内容 */}
                <div className="landing-pipeline__console" aria-hidden="true">
                  {layer.id === 'ebpf' && <EbpfConsole lines={CONSOLE_LINES.ebpf} />}
                  {layer.id === 'rust' && <RustConsole lines={CONSOLE_LINES.rust} />}
                  {layer.id === 'observe' && <ObserveConsole lines={CONSOLE_LINES.observe} />}
                </div>

                <p className="landing-pipeline__desc">{layer.desc}</p>
              </BentoTilt>
              {/* 数据流连接 —— 自下而上流动的发光光线 */}
              {i < ordered.length - 1 && (
                <div className="landing-pipeline__stream" aria-hidden="true">
                  <svg viewBox="0 0 48 56" width="48" height="56">
                    <path d="M24 56 V0" className="landing-pipeline__stream-line" />
                  </svg>
                </div>
              )}
            </div>
          ))}
        </div>
      </FrameMask>
    </section>
  );
}

/** eBPF —— 探针终端：真实日志行 + 闪烁光标 */
function EbpfConsole({ lines }: { lines: string[] }) {
  return (
    <div className="landing-pipeline__console--term">
      {lines.map((l) => (
        <p key={l} className="landing-pipeline__line">
          {l}
        </p>
      ))}
      <p className="landing-pipeline__line landing-pipeline__caret" aria-hidden="true">
        ▍
      </p>
    </div>
  );
}

/** Rust —— 归因图面板：节点沿数据链依次点亮 + 状态行 */
function RustConsole({ lines }: { lines: string[] }) {
  return (
    <div className="landing-pipeline__console--graph">
      <svg viewBox="0 0 320 96" className="landing-pipeline__graph" aria-hidden="true">
        <path
          d="M52 48 C 100 48 100 48 160 48"
          className="landing-pipeline__graph-edge"
          stroke="#6cc2ff"
          strokeWidth="1.5"
          fill="none"
          opacity="0.6"
          strokeDasharray="4 6"
        />
        <path
          d="M160 48 C 220 48 220 48 268 48"
          className="landing-pipeline__graph-edge landing-pipeline__graph-edge--taint"
          stroke="#fbbf24"
          strokeWidth="1.5"
          fill="none"
          opacity="0.55"
          strokeDasharray="4 6"
        />
        <circle cx="52" cy="48" r="7" className="landing-pipeline__graph-node" fill="rgba(108,194,255,0.22)" stroke="#6cc2ff" strokeWidth="2" />
        <circle cx="160" cy="48" r="7" className="landing-pipeline__graph-node" fill="rgba(108,194,255,0.3)" stroke="#6cc2ff" strokeWidth="2" />
        <circle cx="268" cy="48" r="7" className="landing-pipeline__graph-node landing-pipeline__graph-node--src" fill="rgba(251,191,36,0.25)" stroke="#fbbf24" strokeWidth="2" />
        <text x="52" y="26" textAnchor="middle" className="landing-pipeline__graph-label">writer</text>
        <text x="268" y="26" textAnchor="middle" className="landing-pipeline__graph-label">builder</text>
        <text x="160" y="76" textAnchor="middle" className="landing-pipeline__graph-label">taint</text>
      </svg>
      <div className="landing-pipeline__chips">
        {lines.map((l) => (
          <span key={l} className="landing-pipeline__chip">
            {l}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Observe —— 观测仪表盘：告警卡 + 证据卡 + 时间线条 */
function ObserveConsole({ lines }: { lines: string[] }) {
  return (
    <div className="landing-pipeline__console--observe">
      {lines.map((l) => (
        <div key={l} className="landing-pipeline__observe-card">
          <span className="landing-pipeline__observe-dot" aria-hidden="true" />
          <span>{l}</span>
        </div>
      ))}
    </div>
  );
}
