// FileAccessAuditAnalyzer Dashboard — 可视化契约
// 对应 collector/src/framework/analyzers/file_access_audit_analyzer 的输出事件格式
//
// 输出事件 source tag: "file_access"
// 五种访问类型: Read | Write | Execute | Stat | Delete
// 三级风险等级: low | medium | high
// 工作空间边界: inside | outside

import type { AnomalySeverity } from "./data-contract";

// ═══════════════════════════════════════════════════════════════════════════
// 访问类型与风险等级
// ═══════════════════════════════════════════════════════════════════════════

/** 文件访问类型（与 Rust access_type 对齐） */
export type FileAccessType = "Read" | "Write" | "Execute" | "Stat" | "Delete";

/** 风险等级（与 Rust risk_level 对齐） */
export type FileAccessRiskLevel = "low" | "medium" | "high";

/** 工作空间边界（与 Rust workspace_boundary 对齐） */
export type WorkspaceBoundary = "inside" | "outside";

/** 严重程度（与 Rust severity 对齐） */
export type FileAccessSeverity = "info" | "warn";

// ═══════════════════════════════════════════════════════════════════════════
// 标签映射
// ═══════════════════════════════════════════════════════════════════════════

export const FILE_ACCESS_TYPE_LABEL: Record<FileAccessType, string> = {
  Read: "读取",
  Write: "写入",
  Execute: "执行",
  Stat: "状态查询",
  Delete: "删除",
};

export const FILE_ACCESS_TYPE_ICON: Record<FileAccessType, string> = {
  Read: "📖",
  Write: "✏️",
  Execute: "⚡",
  Stat: "🔍",
  Delete: "🗑️",
};

export const FILE_ACCESS_RISK_LABEL: Record<FileAccessRiskLevel, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

export const WORKSPACE_BOUNDARY_LABEL: Record<WorkspaceBoundary, string> = {
  inside: "工作空间内",
  outside: "工作空间外",
};

// ═══════════════════════════════════════════════════════════════════════════
// 核心数据结构
// ═══════════════════════════════════════════════════════════════════════════

/** 文件上下文元数据（与 Rust file_context 对齐，字段均为可选） */
export interface FileContextMeta {
  /** 文件路径 */
  path: string;
  /** 文件权限模式 */
  mode: number | null;
  /** inode 编号（当前 eBPF probe 未产出） */
  inode: number | null;
  /** 文件大小 (bytes)（当前 eBPF probe 未产出） */
  size: number | null;
  /** 文件属主 UID（当前 eBPF probe 未产出） */
  uid: number | null;
  /** 文件属组 GID（当前 eBPF probe 未产出） */
  gid: number | null;
  /** 最后访问时间 (epoch ns)（当前 eBPF probe 未产出） */
  atime: number | null;
  /** 最后修改时间 (epoch ns)（当前 eBPF probe 未产出） */
  mtime: number | null;
}

/**
 * 单次文件访问审计记录（source: "file_access"）
 * 与 Rust FileAccessAuditAnalyzer 产出的 Event.data 结构对齐
 */
export interface FileAccessRecord {
  /** 事件 ID */
  id: string;
  /** 事件类型，固定为 "FILE_OPEN" */
  event: "FILE_OPEN";
  /** 文件绝对路径 */
  filepath: string;
  /** 访问类型 */
  access_type: FileAccessType;
  /** 风险等级 */
  risk_level: FileAccessRiskLevel;
  /** 工作空间边界 */
  workspace_boundary: WorkspaceBoundary;
  /** 严重程度（workspace_boundary == "outside" 时为 "warn"） */
  severity: FileAccessSeverity;
  /** 文件上下文元数据 */
  file_context: FileContextMeta;
  /** 触发此事件的 Agent root PID */
  agent_root_pid: number;
  /** 进程名 */
  comm: string;
  /** 时间戳 (epoch ns) */
  timestamp_ns: number;
}

// ═══════════════════════════════════════════════════════════════════════════
// 聚合统计
// ═══════════════════════════════════════════════════════════════════════════

/** 按访问类型的聚合统计 */
export interface FileAccessTypeStats {
  access_type: FileAccessType;
  /** 总次数 */
  count: number;
  /** 跨边界次数（workspace_boundary == "outside"） */
  cross_boundary_count: number;
  /** 跨边界比例 */
  cross_boundary_ratio: number;
}

/** 按风险等级的聚合统计 */
export interface FileAccessRiskStats {
  risk_level: FileAccessRiskLevel;
  count: number;
  /** 示例文件路径（top-3） */
  top_filepaths: string[];
}

/** 单个 agent 的文件访问摘要 */
export interface AgentFileAccessSummary {
  agent_root_pid: number;
  /** 总访问次数 */
  total_accesses: number;
  /** 跨边界访问次数 */
  cross_boundary_count: number;
  /** 跨边界比例 */
  cross_boundary_ratio: number;
  /** 高危访问次数（风险等级 high） */
  high_risk_count: number;
  /** 按访问类型统计 */
  by_type: FileAccessTypeStats[];
}

/** 工作空间访问全景 */
export interface WorkspaceAccessSnapshot {
  /** 总文件访问事件数 */
  total_events: number;
  /** 工作空间内访问数 */
  inside_workspace: number;
  /** 工作空间外访问数 */
  outside_workspace: number;
  /** 跨边界比例 */
  cross_boundary_ratio: number;
  /** 按风险等级分布 */
  by_risk: FileAccessRiskStats[];
  /** 按访问类型分布 */
  by_type: FileAccessTypeStats[];
  /** 按 agent 的摘要列表 */
  by_agent: AgentFileAccessSummary[];
}

// ═══════════════════════════════════════════════════════════════════════════
// 仪表板状态
// ═══════════════════════════════════════════════════════════════════════════

export type FileAccessViewMode = "overview" | "timeline" | "by_agent" | "by_type";

export interface FileAccessDashboardState {
  /** 原始事件记录列表（最近 N 条） */
  recent_records: FileAccessRecord[];
  /** 工作空间访问全景快照 */
  workspace_snapshot: WorkspaceAccessSnapshot | null;
  /** 当前视图模式 */
  view_mode: FileAccessViewMode;
  /** 选中的记录 ID */
  selected_record_id: string | null;
  /** 访问类型过滤器 */
  type_filter: FileAccessType | null;
  /** 风险等级过滤器 */
  risk_filter: FileAccessRiskLevel | null;
  /** 边界过滤器 */
  boundary_filter: WorkspaceBoundary | null;
  /** agent 过滤器 */
  agent_filter: number | null;
  /** 是否仅显示跨边界事件 */
  show_cross_boundary_only: boolean;
  /** 工作空间根路径列表 */
  workspace_roots: string[];
  /** 最后更新时间 (epoch ms) */
  last_update_ms: number;
}

/** 创建默认状态 */
export function createDefaultFileAccessState(workspace_roots: string[] = []): FileAccessDashboardState {
  return {
    recent_records: [],
    workspace_snapshot: null,
    view_mode: "overview",
    selected_record_id: null,
    type_filter: null,
    risk_filter: null,
    boundary_filter: null,
    agent_filter: null,
    show_cross_boundary_only: false,
    workspace_roots,
    last_update_ms: Date.now(),
  };
}
