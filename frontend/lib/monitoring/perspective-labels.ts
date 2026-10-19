// 监控视角 —— 专业中文标签映射层（纯函数，无 React）。
//
// 后端存储的是内部枚举名（PascalCase / snake_case），直接透传到 UI 会像
// `BelowConfidenceCutoff` / `60s 时间分桶` 一样不专业、难懂。本模块把每个
// 枚举映射为权威中文标签 + 一句话解释，供分布行 / 图表标题 / 提示使用。
// 铁律：未收录的未知值显示原名（诚实），绝不伪造。

/** 映射表：后端值 → 中文标签。优先完整枚举，未知值回退原名。 */
export interface LabelEntry {
  label: string;
  desc?: string;
}

const REASON_LABELS: Record<string, LabelEntry> = {
  NoArgumentMatch: {
    label: '参数不匹配',
    desc: '候选动作的参数与意图调用的参数不一致',
  },
  CandidateNotAvailable: {
    label: '候选不可用',
    desc: '未找到可关联的候选动作',
  },
  BelowConfidenceCutoff: {
    label: '置信度不足',
    desc: '证据关联置信度低于判定阈值',
  },
  OutsideTimeWindow: {
    label: '超出时间窗',
    desc: '候选动作发生在意图时间窗口之外',
  },
  AmbiguousCompetition: {
    label: '候选竞争歧义',
    desc: '多个候选动作证据强度相近，无法唯一确定',
  },
  IntentNotPrimaryEligible: {
    label: '非主意图',
    desc: '该意图不参与主要匹配',
  },
  ObserverPending: {
    label: '观察待定',
    desc: '观察器尚未返回关联结果',
  },
};

const ACTION_TYPE_LABELS: Record<string, LabelEntry> = {
  FileRead: { label: '文件读取', desc: '读取文件内容' },
  FileWrite: { label: '文件写入', desc: '写入文件内容' },
  FileOverwrite: { label: '文件覆写', desc: '覆盖已有文件' },
  ProcessExec: { label: '进程执行', desc: '启动 / 执行进程' },
  FileDelete: { label: '文件删除', desc: '删除文件' },
  FileRename: { label: '文件重命名', desc: '移动 / 重命名文件' },
  NetworkConnect: { label: '网络连接', desc: '建立网络连接' },
  NetworkSend: { label: '网络发送', desc: '发送网络数据' },
};

const SIGNAL_CLASS_LABELS: Record<string, LabelEntry> = {
  HighSignal: { label: '高信号', desc: '强行为证据' },
  LowSignal: { label: '低信号', desc: '弱行为证据' },
};

const BOUNDARY_LABELS: Record<string, LabelEntry> = {
  Capability: { label: '能力边界', desc: '跨能力域的访问' },
  Policy: { label: '策略边界', desc: '违反安全策略' },
  Taint: { label: '污染边界', desc: '污染数据传播' },
  Trust: { label: '信任边界', desc: '跨信任域操作' },
};

const TOOL_LABELS: Record<string, LabelEntry> = {
  Read: { label: '读取', desc: '文件 / 数据读取' },
  Write: { label: '写入', desc: '文件 / 数据写入' },
  Bash: { label: 'Shell 命令', desc: '命令行执行' },
  Agent: { label: '子 Agent', desc: '委派子代理' },
  UserPrompt: { label: '用户指令', desc: '初始任务提示' },
};

const SIGNAL_LABELS: Record<string, LabelEntry> = {
  HighSignal: { label: '高信号', desc: '强行为证据' },
  MediumSignal: { label: '中信号', desc: '中等行为证据' },
  LowSignal: { label: '低信号', desc: '弱行为证据' },
};

/** 通用查表：未收录时回退 [原名, 无描述]。 */
function lookup(table: Record<string, LabelEntry>, raw: string): LabelEntry {
  return table[raw] ?? { label: raw };
}

/** 诊断原因（non_link_diagnostics.reason）→ 中文标签 + 解释。 */
export function reasonLabel(raw: string): LabelEntry {
  return lookup(REASON_LABELS, raw);
}

/** 动作类型（actions.action_type）→ 中文标签。 */
export function actionTypeLabel(raw: string): LabelEntry {
  return lookup(ACTION_TYPE_LABELS, raw);
}

/** 信号类（actions.signal_class）→ 中文标签。 */
export function signalClassLabel(raw: string): LabelEntry {
  return lookup(SIGNAL_CLASS_LABELS, raw);
}

/** 边界（policy_evaluations.boundary）→ 中文标签。 */
export function boundaryLabel(raw: string): LabelEntry {
  return lookup(BOUNDARY_LABELS, raw);
}

/** 工具名（intents.tool_name）→ 中文标签；未知保持原名。 */
export function toolLabel(raw: string): LabelEntry {
  return lookup(TOOL_LABELS, raw);
}

/**
 * 信号（monitoring.health.*.signals 里的派生信号串）→ 中文。
 * 信号串格式为 "告警 14（严重 13）" / "密度 0.8" / "污染 3" —— 已是中文，
 * 保留。此函数仅作未知原始枚举的兜底格式化。
 */
export function signalLabel(raw: string): LabelEntry {
  return lookup(SIGNAL_LABELS, raw);
}
