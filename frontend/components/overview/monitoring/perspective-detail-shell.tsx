'use client';

import type { PerspectiveKey, MonitoringState } from '@/lib/monitoring/monitoring-types';
import { PERSPECTIVE_META } from './perspective-config';
import { renderPerspectiveView } from './perspective-views';

type Props = {
  perspectiveKey: PerspectiveKey;
  monitoring: MonitoringState;
  onBack: () => void;
  onFocusAgent: (agentId: string) => void;
};

/** 详情工作台统一外壳：面包屑 + 标题/描述 + 摘要统计行 + 视图内容。 */
export function PerspectiveDetailShell({
  perspectiveKey,
  monitoring,
  onBack,
  onFocusAgent,
}: Props) {
  const meta = PERSPECTIVE_META[perspectiveKey];
  const h = monitoring.health[perspectiveKey];

  return (
    <div aria-label={`${meta.label}详情`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <button type="button" onClick={onBack} className="ln-btn" aria-label="返回全部">
          ← 返回全部
        </button>
        <span className="ln-eyebrow">运行监控 / {meta.label}</span>
      </div>
      <p
        style={{ margin: '0 0 10px', fontSize: 12, color: 'var(--ln-text-muted)', lineHeight: 1.6 }}
      >
        {meta.description}
      </p>
      {h.available ? (
        <div
          role="group"
          aria-label="摘要统计"
          style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}
        >
          {h.signals.map((s, i) => (
            <span key={i} className="ln-badge" data-tone={i === 0 ? 'info' : 'muted'}>
              {s}
            </span>
          ))}
        </div>
      ) : (
        <div className="ln-badge" data-tone="muted" style={{ marginBottom: 10 }}>
          无此表（旧版数据）——本视角无可用信号
        </div>
      )}
      {renderPerspectiveView(perspectiveKey, monitoring, onFocusAgent)}
    </div>
  );
}
