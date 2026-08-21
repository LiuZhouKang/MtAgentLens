// 实时层类型契约。
// 铁律：高频数据（原始事件/速率统计）不入 zustand，走 LiveSession 环形缓冲 + uSES；
// 低频/结构数据（连接状态/活跃 run/统计摘要）进 store live 切片。

/** SSE 连接状态机：idle 未启用 → connecting → connected / reconnecting → disconnected */
export type LiveConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'disconnected';

/** 活跃 run 元信息（run 生命周期由 /api/runs/active 轮询驱动）。 */
export interface LiveRunMeta {
  runId: string | null;
  startedAtNs: number | null;
  endedAtNs: number | null;
  isActive: boolean;
}

/** 实时统计摘要（低频入 store；原始事件计数为累计值）。 */
export interface LiveStats {
  connectedAtMs: number | null;
  /** 每秒事件数（滑动窗口，实时滚动速率）。 */
  eventRate: number;
  eventCount: number;
  alertCount: number;
  /** SSE lag 累计丢弃数。 */
  droppedCount: number;
  lastEventAtMs: number | null;
}

/** 一条原始实时事件（/api/live/events 的 `event: event` 载荷）。 */
export interface LiveRawEvent {
  /** 后端单调 seq（重连去重用）。 */
  id: string;
  timestampMs: number;
  timestampNs: number;
  source: string;
  pid: number;
  comm: string;
  data: unknown;
}

/** store 的 live 切片。 */
export interface LiveState {
  status: LiveConnectionStatus;
  run: LiveRunMeta;
  stats: LiveStats;
}

export const DEFAULT_LIVE_STATE: LiveState = {
  status: 'idle',
  run: { runId: null, startedAtNs: null, endedAtNs: null, isActive: false },
  stats: {
    connectedAtMs: null,
    eventRate: 0,
    eventCount: 0,
    alertCount: 0,
    droppedCount: 0,
    lastEventAtMs: null,
  },
};
