'use client';

import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import type { PerspectiveKey, MonitoringState } from '@/lib/monitoring/monitoring-types';
import { PERSPECTIVE_META } from './perspective-config';
import { renderPerspectiveView } from './perspective-views';

type Props = {
  perspectiveKey: PerspectiveKey;
  monitoring: MonitoringState;
  onBack: () => void;
  onFocusAgent: (agentId: string) => void;
};

/**
 * 详情工作台统一外壳：面包屑 + 标题/描述 + 摘要统计行 + 视图内容。
 *
 * 切换动画（Linear/Vercel 语言）：monitoring-section 以 key={perspectiveKey}
 * 重挂载本组件，每次视角切换触发入场——
 *  1. 头部（返回+标题）淡入
 *  2. 摘要统计格错峰 blur 浮现
 *  3. 视图内容（统计格/图表卡）错峰上浮 reveal
 * 方向性：整体轻微从右滑入（切换的"换页"感），reduced-motion 直接显示。
 */
export function PerspectiveDetailShell({
  perspectiveKey,
  monitoring,
  onBack,
  onFocusAgent,
}: Props) {
  const meta = PERSPECTIVE_META[perspectiveKey];
  const h = monitoring.health[perspectiveKey];
  const rootRef = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const root = rootRef.current;
      if (!root) return;
      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

      // 1. 头部 + 描述 + 统计行：从右轻滑入 + 淡入
      const head = root.querySelectorAll('.ln-detail-head, .ln-detail-desc, .ln-detail-stats');
      gsap.from(head, {
        opacity: 0,
        x: 18,
        duration: 0.4,
        stagger: 0.06,
        ease: 'power2.out',
      });

      // 2. 视图内容：错峰上浮 reveal（统计格/图表卡逐层出现）
      const blocks = root.querySelectorAll('.ln-detail-block');
      gsap.from(blocks, {
        opacity: 0,
        y: 18,
        filter: 'blur(3px)',
        duration: 0.5,
        stagger: 0.07,
        ease: 'power3.out',
        delay: 0.15,
        clearProps: 'transform, filter',
      });
    },
    { scope: rootRef, dependencies: [perspectiveKey] },
  );

  return (
    <div ref={rootRef} aria-label={`${meta.label}详情`}>
      <div className="ln-detail-head" style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <button type="button" onClick={onBack} className="ln-btn" aria-label="返回全部">
          ← 返回全部
        </button>
        <span className="ln-eyebrow">运行监控 / {meta.label}</span>
      </div>
      <p
        className="ln-detail-desc"
        style={{ margin: '0 0 10px', fontSize: 12, color: 'var(--ln-text-muted)', lineHeight: 1.6 }}
      >
        {meta.description}
      </p>
      {h.available ? (
        <div
          className="ln-detail-stats"
          role="group"
          aria-label="摘要统计"
          style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}
        >
          {h.signals.map((s, i) => (
            <span key={s} className="ln-badge" data-tone={i === 0 ? 'info' : 'muted'}>
              {s}
            </span>
          ))}
        </div>
      ) : (
        <div className="ln-badge" data-tone="muted" style={{ marginBottom: 10 }}>
          无此表（旧版数据）——本视角无可用信号
        </div>
      )}
      <div className="ln-detail-block">
        {renderPerspectiveView(perspectiveKey, monitoring, onFocusAgent)}
      </div>
    </div>
  );
}
