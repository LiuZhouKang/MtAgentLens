// 回溯链 —— 可读化纯函数层（无 React）。
//
// 后端 responsibility_tracebacks.explanation_summary 是 Rust 调试格式：
//   "Traceback for claim claim:xxx: Tainted Context Use (confidence 68%)
//    Causal chain:
//      [start] Agent(9982971f-50e)
//      caused→ Resource(File { canonical_path: "/" })
//      crossed boundary→ Resource(File { canonical_path: ".." })
//    Root cause: Agent(99e353da-d6e)
//    Supporting evidence: 6 record(s)"
// 直接透传 UI 普通用户完全看不懂。本模块把它解析为结构化的因果链步骤，
// 供前端渲染为「AgentAvatar + 图标 + 可读标签 + 箭头流程」的可视化因果链。
// 真实 db 的 chain_node_ids 常为 null，因此以解析 explanation_summary 为主；
// 若 chain_node_ids 非空（测试 / 部分数据），组件可走结构化路径。
// 纯函数：只依赖输入，无副作用。

export type TracebackNodeKind =
  | 'agent'
  | 'file'
  | 'process'
  | 'network'
  | 'tool'
  | 'credential'
  | 'context'
  | 'llm'
  | 'unknown';

export interface TracebackStep {
  kind: TracebackNodeKind;
  /** 可读标签（shortAgentId / 文件路径 / pid N / host:port / 工具名） */
  label: string;
  /** 附加信息（完整 agent id / 网络端口等），hover 展示 */
  detail: string | null;
  /** crossed boundary → 语义强化 */
  isBoundary: boolean;
}

export interface TracebackVisual {
  steps: TracebackStep[];
  /** 根因节点（可能不在 steps 内） */
  rootCause: TracebackStep | null;
  /** Supporting evidence: N record(s) */
  evidenceCount: number | null;
}

// ── 节点行解析：Agent(xxx) / Resource(Type { ... }) ──

const AGENT_RE = /^Agent\(([^)]+)\)$/;
const FILE_RE = /^Resource\(File\s*\{\s*canonical_path:\s*"([^"]*)"\s*\}\)$/;
const PROCESS_RE =
  /^Resource\(Process\s*\{\s*process_id:\s*ProcessId\s*\{\s*pid:\s*(\d+)(?:,\s*start_time_ns:\s*(\d+))?\s*\}\s*\}\)$/;

/** 节点类型 → 中文小标签（供因果链类型徽章）。 */
export const TRACEBACK_KIND_LABEL: Record<TracebackNodeKind, string> = {
  agent: 'Agent',
  file: '文件',
  process: '进程',
  network: '网络',
  tool: '工具',
  credential: '凭证',
  context: '上下文',
  llm: 'LLM',
  unknown: '未知',
};
const NETWORK_RE =
  /^Resource\(Network\s*\{\s*host:\s*"([^"]*)",\s*port:\s*(\d+)(?:,\s*scheme:\s*"([^"]*)")?\s*\}\)$/;
const TOOL_RE = /^Resource\(ToolCall\s*\{\s*tool_name:\s*"([^"]*)"(?:,\s*call_id:\s*"([^"]*)")?\s*\}\)$/;
const CREDENTIAL_RE = /^Resource\(Credential\s*\{\s*credential_id:\s*"([^"]*)"\s*\}\)$/;
const CONTEXT_RE = /^Resource\(Context\s*\{\s*context_id:\s*"([^"]*)"\s*\}\)$/;
const LLM_RE =
  /^Resource\(LlmConversation\s*\{\s*provider:\s*"([^"]*)",\s*model:\s*"([^"]*)"(?:,\s*message_id:\s*"([^"]*)")?\s*\}\)$/;

/** 把 Rust debug 格式的节点内容解析为可读步骤。 */
function parseNode(raw: string): TracebackStep {
  const agent = raw.match(AGENT_RE);
  if (agent) {
    // 保留完整 agent id（因果链步骤少，完整 id 可读且权威）
    const id = agent[1];
    return { kind: 'agent', label: id, detail: id, isBoundary: false };
  }
  const file = raw.match(FILE_RE);
  if (file) {
    return { kind: 'file', label: file[1] || '/', detail: null, isBoundary: false };
  }
  const process = raw.match(PROCESS_RE);
  if (process) {
    return {
      kind: 'process',
      label: `进程 pid ${process[1]}`,
      detail: process[2] ? `start_time: ${process[2]}` : null,
      isBoundary: false,
    };
  }
  const network = raw.match(NETWORK_RE);
  if (network) {
    return {
      kind: 'network',
      label: `${network[1]}:${network[2]}`,
      detail: raw,
      isBoundary: false,
    };
  }
  const tool = raw.match(TOOL_RE);
  if (tool) {
    return { kind: 'tool', label: tool[1], detail: tool[2] ?? null, isBoundary: false };
  }
  const credential = raw.match(CREDENTIAL_RE);
  if (credential) {
    return { kind: 'credential', label: credential[1], detail: raw, isBoundary: false };
  }
  const context = raw.match(CONTEXT_RE);
  if (context) {
    return { kind: 'context', label: context[1], detail: raw, isBoundary: false };
  }
  const llm = raw.match(LLM_RE);
  if (llm) {
    return { kind: 'llm', label: `${llm[1]}/${llm[2]}`, detail: raw, isBoundary: false };
  }
  // 未知格式：诚实展示原文（截断）
  return { kind: 'unknown', label: raw.slice(0, 80), detail: raw, isBoundary: false };
}

/**
 * 解析 explanation_summary → 结构化因果链。
 * 无法识别（空 / 非该格式）→ null（组件走链节点 / 空态兜底）。
 */
export function parseTracebackSummary(summary: string): TracebackVisual | null {
  if (!summary) return null;
  const lines = summary.split('\n');
  const steps: TracebackStep[] = [];
  let rootCause: TracebackStep | null = null;
  let evidenceCount: number | null = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    if (line.startsWith('Traceback for claim')) continue;
    if (line.startsWith('Causal chain:')) continue;

    const root = line.match(/^Root cause:\s*(.+)$/);
    if (root) {
      rootCause = parseNode(root[1]);
      continue;
    }
    const ev = line.match(/^Supporting evidence:\s*(\d+)\s*record/i);
    if (ev) {
      evidenceCount = Number(ev[1]);
      continue;
    }
    // 因果链步骤：[start] X / caused→ X / crossed boundary→ X
    const boundary = line.match(/^(?:\[start\]|crossed\s+boundary→)\s*(.+)$/);
    if (boundary) {
      const step = parseNode(boundary[1]);
      steps.push({ ...step, isBoundary: step.isBoundary || line.includes('boundary') });
      continue;
    }
    const caused = line.match(/^caused→\s*(.+)$/);
    if (caused) {
      steps.push(parseNode(caused[1]));
    }
  }

  if (steps.length === 0 && !rootCause) return null;
  return { steps, rootCause, evidenceCount };
}
