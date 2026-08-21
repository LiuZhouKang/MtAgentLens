// 告警调查页 —— 专业中文标签映射层（纯函数，无 React）。
//
// 与 perspective-labels.ts 同一铁律：后端存储内部枚举名（PascalCase /
// UPPER_SNAKE / snake_case），直接透传到 UI 会像 `TaintedContextUse` /
// `L1Exact` / `Authoritative` 一样不专业。本模块把告警调查工作台涉及的全部
// 枚举映射为权威中文标签 + 一句话解释 + 状态 tone，供徽章 / 面板标题 / 时间线使用。
// 未收录的未知值显示原名（诚实），绝不伪造。

/** 通用标签项：中文标签 + 可选解释 + 可选状态 tone（供 StatusBadge / 色点）。 */
export interface InvLabel {
  label: string;
  desc?: string;
  /** status-badge tone：success/danger/warning/info/muted。缺失 → muted。 */
  tone?: 'success' | 'danger' | 'warning' | 'info' | 'muted';
}

function lookup(table: Record<string, InvLabel>, raw: string, fallbackTone?: InvLabel['tone']): InvLabel {
  return table[raw] ?? { label: raw, tone: fallbackTone };
}

/** StatusTone → ln token 颜色。徽章色点 / 时间线节点 / 图例共用，保证全站同色。 */
export const TONE_COLOR: Record<NonNullable<InvLabel['tone']>, string> = {
  success: 'var(--ln-success)',
  danger: 'var(--ln-danger)',
  warning: 'var(--ln-warning)',
  info: 'var(--ln-info)',
  muted: 'var(--ln-text-muted)',
};

// ═══ 判决 / 严重度 ═══

const DECISION_LABELS: Record<string, InvLabel> = {
  Unsafe: { label: '需处置', desc: '引擎判定存在未受控风险，需要处置', tone: 'danger' },
  Uncertain: { label: '待复核', desc: '证据不足以定论，需要人工复核', tone: 'warning' },
  Safe: { label: '已受控', desc: '行为处于受控基线，无活跃风险', tone: 'success' },
};

const SEVERITY_LABELS: Record<string, InvLabel> = {
  Critical: { label: '严重', desc: '高危风险，需立即关注', tone: 'danger' },
  Warning: { label: '警告', desc: '潜在风险，建议核查', tone: 'warning' },
  Info: { label: '提示', desc: '信息级观察', tone: 'info' },
};

const AUTHORITY_LABELS: Record<string, InvLabel> = {
  Authoritative: { label: '权威', desc: '身份升级为确定性权威身份', tone: 'success' },
  Supporting: { label: '佐证', desc: '身份由多条信号共同佐证', tone: 'info' },
  Unavailable: { label: '不可用', desc: '身份信号不足，暂不可靠', tone: 'muted' },
};

const DISPLAY_PROFILE_LABELS: Record<string, InvLabel> = {
  SafeBaseline: { label: '安全基线', desc: '处于安全行为基线内' },
  UnsafeByEngine: { label: '引擎判定风险', desc: '引擎规则/模式判定存在风险' },
  ObserverJudged: { label: '观察器判定', desc: 'LLM 观察器补充判定' },
};

// ═══ 责任声明 ═══

const CLAIM_TYPE_LABELS: Record<string, InvLabel> = {
  PolicyViolation: { label: '策略违规', desc: '违反安全策略', tone: 'danger' },
  AccountabilityAnomaly: { label: '责任异常', desc: '责任归属出现异常', tone: 'warning' },
  SuspiciousDelegation: { label: '可疑委派', desc: '存在可疑的委派关系', tone: 'warning' },
  TaintedContextUse: { label: '污染上下文使用', desc: '使用了被污染的数据上下文', tone: 'danger' },
  UnauthorizedCapabilityUse: { label: '越权能力使用', desc: '超出授予能力范围的操作', tone: 'danger' },
  BlockedAttempt: { label: '已阻断尝试', desc: '越权行为已被阻断', tone: 'info' },
};

const CLAIM_STATE_LABELS: Record<string, InvLabel> = {
  Inferred: { label: '推断', desc: '由证据链推断得出', tone: 'info' },
  Confirmed: { label: '已确认', desc: '多路证据交叉确认', tone: 'success' },
  Ambiguous: { label: '歧义', desc: '证据不足或互相矛盾', tone: 'warning' },
};

// ═══ 证据 ═══

const MATCH_LEVEL_LABELS: Record<string, InvLabel> = {
  L1Exact: { label: '精确匹配', desc: '证据字段完全一致', tone: 'success' },
  L2Normalized: { label: '归一化匹配', desc: '规范化后一致', tone: 'info' },
  L3Containment: { label: '包含匹配', desc: '一方包含另一方', tone: 'info' },
  L4Basename: { label: '基名匹配', desc: '仅基名一致', tone: 'warning' },
  L5Substring: { label: '子串匹配', desc: '子串级弱匹配', tone: 'warning' },
};

const DERIVATION_MODE_LABELS: Record<string, InvLabel> = {
  Direct: { label: '直接推导', desc: '由意图直接推导' },
  Indirect: { label: '间接推导', desc: '经中间载体间接推导' },
  Observed: { label: '观察器捕获', desc: '由观察器捕获' },
  LLM: { label: 'LLM 归因', desc: '由 LLM 语义归因' },
  Demo: { label: '演示数据', desc: '示例/演示生成' },
};

// ═══ 引擎判定 ═══

const VERDICT_KIND_LABELS: Record<string, InvLabel> = {
  rule: { label: '规则判定', desc: '基于规则引擎判定', tone: 'info' },
  pattern: { label: '模式判定', desc: '基于行为模式判定', tone: 'warning' },
};

// ═══ 时间线事件 ═══
//
// 覆盖两套取值：demo 数据的 snake_case（agent_start）与真实后端的
// UPPER_SNAKE / PascalCase（EXEC / CREDENTIALACCESS）。查找顺序：
// 精确匹配 → 大小写归一化匹配 → 未知回退原名。映射不完备没关系——诚实回退。

const EVENT_TYPE_LABELS: Record<string, InvLabel> = {
  // demo snake_case
  agent_start: { label: '主体启动', desc: 'Agent 进程启动' },
  delegation: { label: '委派', desc: '向下级 Agent 委派任务' },
  role_claim: { label: '角色声明', desc: '声明执行角色' },
  tool_call: { label: '工具调用', desc: '调用外部工具' },
  file_read: { label: '文件读取', desc: '读取文件内容' },
  file_write: { label: '文件写入', desc: '写入文件内容' },
  boundary_cross: { label: '边界穿越', desc: '跨越安全边界' },
  network_connect: { label: '网络连接', desc: '建立网络连接' },
  signal: { label: 'SSL/TLS 连接', desc: '加密连接信号' },
  // 真实后端 UPPER_SNAKE / PascalCase
  EXEC: { label: '进程执行', desc: '启动 / 执行进程' },
  PROCESS_EXEC: { label: '进程执行', desc: '启动 / 执行进程' },
  EXIT: { label: '进程退出', desc: '进程终止' },
  HANDOFF_SPAWN: { label: '委派派生', desc: '委派并派生子进程' },
  BASH_READLINE: { label: 'Shell 输入', desc: 'Shell 读取命令' },
  FILE_OPEN: { label: '文件打开', desc: '打开文件' },
  FILE_WRITE: { label: '文件写入', desc: '写入文件内容' },
  FILE_DELETE: { label: '文件删除', desc: '删除文件' },
  FILE_RENAME: { label: '文件重命名', desc: '重命名文件' },
  FILE_TRUNCATE: { label: '文件截断', desc: '截断文件' },
  NETWORK_CONNECT: { label: '网络连接', desc: '建立网络连接' },
  NETWORK_SEND: { label: '网络发送', desc: '发送网络数据' },
  NETWORK_RECV: { label: '网络接收', desc: '接收网络数据' },
  INJECTION_DETECTED: { label: '注入检测', desc: '检测到注入攻击', tone: 'danger' },
  IDENTITY_CONFUSION: { label: '身份混淆', desc: '检测到身份混淆', tone: 'danger' },
  CONTEXT_POLLUTION_DETECTED: { label: '上下文污染', desc: '检测到上下文被污染', tone: 'danger' },
  CREDENTIAL_LEAK_DETECTED: { label: '凭证泄露', desc: '检测到凭证泄露', tone: 'danger' },
  ANOMALY: { label: '行为异常', desc: '检测到异常行为', tone: 'danger' },
  ALERT: { label: '告警', desc: '责任告警触发', tone: 'danger' },
  SUBPROCESS_ALERT: { label: '子进程告警', desc: '子进程责任告警', tone: 'danger' },
  SIGNOFF_TRANSITION: { label: '阶段移交', desc: '责任阶段移交', tone: 'info' },
  CREDENTIALACCESS: { label: '凭证访问', desc: '访问凭证资源', tone: 'danger' },
  CONTEXTINJECTION: { label: '上下文注入', desc: '注入上下文数据', tone: 'danger' },
  SUMMARY: { label: '阶段总结', desc: '阶段汇总事件', tone: 'info' },
  CONTROL: { label: '控制事件', desc: '运行控制事件', tone: 'info' },
  CLOCK_SYNC: { label: '时钟同步', desc: '时间基准同步', tone: 'muted' },
};

// ═══ 通用查表 ═══

/** 判决（Unsafe/Uncertain/Safe）→ 中文标签 + tone。 */
export function decisionLabel(raw: string): InvLabel {
  return lookup(DECISION_LABELS, raw);
}

/** 严重度（Critical/Warning/Info）→ 中文标签 + tone。 */
export function severityLabel(raw: string): InvLabel {
  return lookup(SEVERITY_LABELS, raw);
}

/** 身份权威度（Authoritative/Supporting/Unavailable）→ 中文标签 + tone。 */
export function authorityLabel(raw: string): InvLabel {
  return lookup(AUTHORITY_LABELS, raw);
}

/** 展示画像（SafeBaseline/UnsafeByEngine/ObserverJudged）→ 中文标签。 */
export function displayProfileLabel(raw: string): InvLabel {
  return lookup(DISPLAY_PROFILE_LABELS, raw);
}

/** 声明类型（PolicyViolation/TaintedContextUse/…）→ 中文标签 + tone。 */
export function claimTypeLabel(raw: string): InvLabel {
  return lookup(CLAIM_TYPE_LABELS, raw);
}

/** 声明状态（Inferred/Confirmed/Ambiguous）→ 中文标签 + tone。 */
export function claimStateLabel(raw: string): InvLabel {
  return lookup(CLAIM_STATE_LABELS, raw);
}

/** 证据匹配层级（L1Exact/L2Normalized/…）→ 中文标签 + tone。 */
export function matchLevelLabel(raw: string): InvLabel {
  return lookup(MATCH_LEVEL_LABELS, raw);
}

/** 证据推导方式（Direct/Indirect/…）→ 中文标签（大小写不敏感）。 */
export function derivationModeLabel(raw: string): InvLabel {
  const direct = DERIVATION_MODE_LABELS[raw];
  if (direct) return direct;
  const lower = raw.toLowerCase();
  for (const key of Object.keys(DERIVATION_MODE_LABELS)) {
    if (key.toLowerCase() === lower) return DERIVATION_MODE_LABELS[key];
  }
  return { label: raw };
}

/** 引擎判定种类（rule/pattern）→ 中文标签。 */
export function verdictKindLabel(raw: string): InvLabel {
  return lookup(VERDICT_KIND_LABELS, raw);
}

/** 时间线事件类型（大小写不敏感）→ 中文标签 + tone。 */
export function eventTypeLabel(raw: string): InvLabel {
  const hit = EVENT_TYPE_LABELS[raw] ?? EVENT_TYPE_LABELS[raw.toUpperCase()];
  return hit ?? { label: raw };
}
