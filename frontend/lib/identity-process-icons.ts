import type { ComponentType } from 'react';
import {
  Archive,
  Bot,
  Box,
  Braces,
  CircleDot,
  Cpu,
  Database,
  FileCode,
  FileText,
  Folder,
  GitBranch,
  GitFork,
  Globe,
  Network,
  Package,
  Search,
  Server,
  Settings,
  ShieldCheck,
  Terminal,
  Wrench,
} from 'lucide-react';

/**
 * 进程 comm → 权威图标 + 品牌色（纯函数层，无 React 副作用）。
 *
 * 让进程谱系中的常用进程拥有专业、可识别的视觉身份（而非纯文字），
 * 与全站 AgentAvatar 品牌体系呼应。未知 comm 回退通用图标 + 中性色（诚实）。
 */

export interface ProcessIconSpec {
  /** lucide 图标组件 */
  Icon: ComponentType<{ size?: number; strokeWidth?: number }>;
  /** 品牌色（图标渲染色） */
  color: string;
}

/** 品牌色表 —— 与 AGENT_PALETTE / 全站深空配色同源 */
const C = {
  amber: '#fbbf24',
  cyan: '#6cc2ff',
  violet: '#8b7dff',
  green: '#4ade80',
  rose: '#ff6b9d',
  orange: '#ffb454',
  sky: '#56ccf2',
  coral: '#ff8a5c',
  indigo: '#5e6ad2',
  slate: '#9aa5b1',
};

/** comm → 图标规格。前缀匹配（如 mcp-server-gith / register-python）。 */
const COMM_SPECS: Array<{ match: RegExp; spec: ProcessIconSpec }> = [
  { match: /^(sh|bash|zsh|fish)$/, spec: { Icon: Terminal, color: C.cyan } },
  { match: /^node$/, spec: { Icon: Braces, color: C.green } },
  { match: /^npm$/, spec: { Icon: Package, color: C.coral } },
  { match: /^npx$/, spec: { Icon: Package, color: C.coral } },
  { match: /^(claude|claude-code|codex)$/, spec: { Icon: Bot, color: C.amber } },
  { match: /^mcp-server/, spec: { Icon: Server, color: C.amber } },
  { match: /^git$/, spec: { Icon: GitBranch, color: C.orange } },
  { match: /^codegraph$/, spec: { Icon: GitFork, color: C.violet } },
  { match: /^sudo$/, spec: { Icon: ShieldCheck, color: C.rose } },
  { match: /^(ss|netstat|curl|wget)$/, spec: { Icon: Network, color: C.sky } },
  { match: /^(grep|sed|awk|sort|head|tail|diff|rg)$/, spec: { Icon: Search, color: C.slate } },
  { match: /^(python|python3|node-gyp|register-python)/, spec: { Icon: FileCode, color: C.indigo } },
  { match: /^(tar|gzip|zip|unzip|7z)/, spec: { Icon: Archive, color: C.slate } },
  { match: /^(mkdir|mkdirp)$/, spec: { Icon: Folder, color: C.amber } },
  { match: /^(cat|rm|ls|cp|mv|touch|file)$/, spec: { Icon: FileText, color: C.slate } },
  { match: /^(ps|top|htop|uptime)$/, spec: { Icon: Cpu, color: C.green } },
  { match: /^(docker|kubectl|podman)$/, spec: { Icon: Box, color: C.sky } },
  { match: /^(make|cmake|cargo|go)$/, spec: { Icon: Wrench, color: C.orange } },
  { match: /^(env|printenv|which|whereis|manpath)$/, spec: { Icon: Settings, color: C.slate } },
  { match: /^(sqlite|psql|mysql|redis-cli)$/, spec: { Icon: Database, color: C.violet } },
  { match: /^(curl|ssh|scp|ping)$/, spec: { Icon: Globe, color: C.cyan } },
];

/** 未知 comm 的通用回退 */
const DEFAULT_SPEC: ProcessIconSpec = { Icon: CircleDot, color: C.slate };

/** comm → 权威图标规格（未知回退通用）。 */
export function processIconSpec(comm: string): ProcessIconSpec {
  for (const { match, spec } of COMM_SPECS) {
    if (match.test(comm)) return spec;
  }
  return DEFAULT_SPEC;
}
