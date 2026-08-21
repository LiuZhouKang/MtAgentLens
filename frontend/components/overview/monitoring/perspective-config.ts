import type { PerspectiveKey } from '@/lib/monitoring/monitoring-types';

export const PERSPECTIVE_META: Record<PerspectiveKey, { label: string; description: string }> = {
  topologyDrift: {
    label: '拓扑漂移',
    description:
      '责任网络随时间的演进：节点 / 边 / Agent 逐帧变化，以及首末状态的差分。',
  },
  topologyHealth: {
    label: '拓扑健康',
    description: '最新拓扑足迹、告警严重度与上下文污染，衡量网络生长与运行健康。',
  },
  intentMismatch: {
    label: '意图-动作失配',
    description: '意图与动作的失配诊断量、成因分布，以及证据匹配强度（弱 / 强）。',
  },
  policyCompliance: {
    label: '策略合规',
    description: '策略评估中的真实违规率与边界分布，识别越界行为。',
  },
  toolFootprint: {
    label: '工具足迹',
    description: '意图工具调用、动作类型、信号强度与高频文件路径。',
  },
  networkSessions: {
    label: '网络会话',
    description: 'TLS 会话总量、按分钟的会话趋势与按进程的分布。',
  },
};

export const PERSPECTIVE_ORDER: PerspectiveKey[] = [
  'topologyDrift',
  'topologyHealth',
  'intentMismatch',
  'policyCompliance',
  'toolFootprint',
  'networkSessions',
];
