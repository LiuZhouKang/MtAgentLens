import type { PerspectiveKey } from '@/lib/monitoring/monitoring-types'

export const PERSPECTIVE_META: Record<PerspectiveKey, { label: string; description: string }> = {
  topologyDrift: {
    label: '拓扑漂移',
    description: 'graph_snapshots.metrics_json 逐帧演进：节点/边/agent 数随快照帧的变化与首末差分。',
  },
  topologyHealth: {
    label: '拓扑健康',
    description: '最新帧拓扑足迹 + 告警严重度 + 上下文污染，度量拓扑生长而非时延健康。',
  },
  intentMismatch: {
    label: '意图-动作失配',
    description: 'non_link_diagnostics 诊断量、按 reason 分布与证据匹配强度（L5Substring 为弱证据）。',
  },
  policyCompliance: {
    label: '策略合规',
    description: 'policy_evaluations 中 judgment_json.result=Violation 的真实违规率与边界分布。',
  },
  toolFootprint: {
    label: '工具足迹',
    description: '意图工具调用 / 动作类型 / 信号类 / top 文件路径（normalized_args.Path）。',
  },
  networkSessions: {
    label: '网络会话',
    description: 'tls_sessions 会话计数、60s 时间分桶与按 pid 分布。',
  },
}

export const PERSPECTIVE_ORDER: PerspectiveKey[] = [
  'topologyDrift',
  'topologyHealth',
  'intentMismatch',
  'policyCompliance',
  'toolFootprint',
  'networkSessions',
]

/** 阶段二实时视角（SSE 占位）——静态文案，不接入数据。 */
export const PHASE2_PILLS = [
  'LLM API 追踪',
  'LLM API 异常',
  '数据外泄',
  'Agent 循环',
  '会话健康（实时五维）',
  'SSE 内容扫描',
  '去重诊断',
  '分析器流水线',
]
