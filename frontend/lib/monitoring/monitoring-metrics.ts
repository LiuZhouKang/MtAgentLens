// 运行监控 —— 每视角「核心指标」纯函数派生层。
// 铁律：任何主数字/副指标/迷你趋势都由真实数据派生；算不出的诚实留空（null）。
// 供深空监测仪表卡消费（monitoring-card.tsx）。

import type { MonitoringState, PerspectiveKey } from './monitoring-types';

export interface PerspectiveMetric {
  /** 主数字（mono 大字）：该视角最有信息量的单一数字。无真实数字 = null（不造数）。 */
  value: number | string | null;
  /** 主数字单位（如 节点 / 诊断 / 会话），无单位 = null */
  unit?: string | null;
  /** 副指标行（mono 小字）：信号字符串，可空 */
  secondary?: string | null;
  /** 迷你趋势：真实时间序列（有帧/分桶才有），无 = null */
  trend?: Array<Record<string, number | string>> | null;
  /** 趋势系列 key（默认 value） */
  trendKey?: string;
  /** 趋势是否有意义（≥2 点） */
  hasTrend?: boolean;
}

/** 6 视角核心指标的统一派生入口 —— 每视角从真实 state 提取主数字 + 副指标 + 趋势。 */
export function derivePerspectiveMetric(
  key: PerspectiveKey,
  monitoring: MonitoringState,
): PerspectiveMetric {
  switch (key) {
    case 'topologyDrift': {
      const s = monitoring.topologyDrift;
      if (!s || !s.latest) return { value: null };
      const frames = s.frames;
      const trend = frames.map((f, i) => ({ name: String(i + 1), value: f.nodeCount }));
      const delta = s.delta ? (s.delta.nodes >= 0 ? `+${s.delta.nodes}` : `${s.delta.nodes}`) : null;
      return {
        value: s.latest.nodeCount,
        unit: '节点',
        secondary: delta
          ? `${frames.length} 帧 · Δ${delta}`
          : `${frames.length} 帧 · 无差分`,
        trend,
        trendKey: 'value',
        hasTrend: trend.length >= 2,
      };
    }

    case 'topologyHealth': {
      const s = monitoring.topologyHealth;
      if (!s) return { value: null };
      const totalAlerts = s.alertSeverity.critical + s.alertSeverity.warning + s.alertSeverity.info;
      return {
        value: totalAlerts,
        unit: '告警',
        secondary: `污染 ${s.pollutionCount} · 链 ${s.latest?.taintChains ?? '—'}`,
        trend: null,
      };
    }

    case 'intentMismatch': {
      const s = monitoring.intentMismatch;
      if (!s) return { value: null };
      const density =
        s.mismatchDensity != null ? `密度 ${s.mismatchDensity.toFixed(1)}` : '无意图';
      return {
        value: s.diagnosticsCount,
        unit: '诊断',
        secondary: `${density} · 弱 ${s.evidenceMatch.weak}/${s.evidenceMatch.total}`,
        trend: null,
      };
    }

    case 'policyCompliance': {
      const s = monitoring.policyCompliance;
      if (!s) return { value: null };
      const ratio =
        s.violationRatio != null ? `${(s.violationRatio * 100).toFixed(1)}%` : null;
      return {
        value: ratio ?? s.violations,
        unit: ratio != null ? null : '违规',
        secondary: `${s.violations}/${s.evaluationsTotal} 违规`,
        trend: null,
      };
    }

    case 'toolFootprint': {
      const s = monitoring.toolFootprint;
      if (!s) return { value: null };
      return {
        value: s.intentsCount,
        unit: '意图',
        secondary: `${s.actionsCount} 动作 · ${s.topFiles.length} 文件`,
        trend: null,
      };
    }

    case 'networkSessions': {
      const s = monitoring.networkSessions;
      if (!s) return { value: null };
      const trend = s.trend.map((b, i) => ({ name: String(i + 1), value: b.count }));
      return {
        value: s.total,
        unit: '会话',
        secondary: `${Object.keys(s.byPid).length} 进程`,
        trend,
        trendKey: 'value',
        hasTrend: trend.length >= 2,
      };
    }
  }
}
