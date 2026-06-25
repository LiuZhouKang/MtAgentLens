#!/usr/bin/env python3
"""
generate_case_studies.py — 生成 3 个 Type C 案例研究的责任图可视化。

从 responsibility.db 读取图节点和边,构建 Mermaid 格式图输出到
compare/results/paper/figures/case_study_{scenario_id}.md。

所有路径由 CASES 配置列表驱动,不硬编码在逻辑中。
"""

import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

# ─── 配置:案例定义 ───────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # MtAgentLens/
COMPARE_DIR = PROJECT_ROOT / "compare"

CASES = [
    {
        "scenario_id": "collusion-ring",
        "db_path": str(
            COMPARE_DIR
            / "Dataset/FuxiBench/traces/agent_teams/collusion-ring/responsibility.db"
        ),
        "topology": "hub-and-spoke",
        "title": "Case Study 3.1: Collusion Ring",
        "description": (
            "三个 agent 协同访问共享资源,每个 agent 同时对多个"
            "PolicyBoundary 节点施加 IntentCausesAction 和 PolicyBoundaryCrossing "
            "边,展现出「群组协同越权」的星型拓扑。"
        ),
    },
    {
        "scenario_id": "delegation-chain",
        "db_path": str(
            COMPARE_DIR
            / "Dataset/FuxiBench/traces/subagents/delegation-chain/responsibility.db"
        ),
        "topology": "chain",
        "title": "Case Study 3.2: Delegation Chain",
        "description": (
            "父 agent 逐级委托子 agent 执行敏感操作,"
            "形成深度为 3+ 的委托链,每个节点仅与上下游相连。"
        ),
    },
    {
        "scenario_id": "handoff-injection",
        "db_path": str(
            COMPARE_DIR
            / "Dataset/FuxiBench/traces/agent_teams/handoff-injection/responsibility.db"
        ),
        "topology": "communication-injection",
        "title": "Case Study 3.3: Handoff Injection",
        "description": (
            "多个 agent 通过 handoff 消息注入恶意 payload,"
            "在 agent 间通信链路上传播,攻击者 agent 向良性 agent "
            "的 PolicyBoundary 注入伪造的 IntentCausesAction 边。"
        ),
    },
]

OUTPUT_DIR = COMPARE_DIR / "results/paper/figures"


# ─── 数据库查询层 ────────────────────────────────────────────────────────────


def get_connection(db_path: str) -> Optional[sqlite3.Connection]:
    """打开 SQLite 连接,文件不存在时返回 None。"""
    if not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_run_id(conn: sqlite3.Connection) -> Optional[str]:
    """获取最近的 run_id。"""
    row = conn.execute(
        "SELECT run_id FROM runs ORDER BY started_at_ns DESC LIMIT 1"
    ).fetchone()
    return row["run_id"] if row else None


def get_agent_nodes(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """获取所有 Agent 类型节点。"""
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM graph_nodes WHERE node_type = 'Agent' AND run_id = ?",
            (run_id,),
        )
    ]


def get_all_nodes(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """获取所有节点。"""
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM graph_nodes WHERE run_id = ?", (run_id,)
        )
    ]


def get_all_edges(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """获取所有边。"""
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM graph_edges WHERE run_id = ?", (run_id,)
        )
    ]


def get_agent_process_map(conn: sqlite3.Connection, run_id: str) -> dict[str, list[str]]:
    """获取 agent_id → [comm, ...] 映射(去重后最常见的进程名)。"""
    rows = conn.execute(
        """
        SELECT ipm.agent_id, pe.comm
        FROM identity_process_map ipm
        JOIN process_events pe
          ON ipm.pid = pe.pid AND ipm.start_time_ns = pe.start_time_ns
        WHERE ipm.run_id = ?
        """,
        (run_id,),
    ).fetchall()

    agent_comms: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        agent_comms[row["agent_id"]].append(row["comm"])
    return dict(agent_comms)


def get_delegation_edges(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    """获取委托记录表(如果存在)。"""
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM delegation_records WHERE run_id = ?", (run_id,)
            )
        ]
    except sqlite3.OperationalError:
        return []


def get_graph_counts(conn: sqlite3.Connection, run_id: str) -> dict:
    """统计图基本指标。"""
    node_count = conn.execute(
        "SELECT COUNT(*) FROM graph_nodes WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    edge_count = conn.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    node_types = dict(
        conn.execute(
            "SELECT node_type, COUNT(*) FROM graph_nodes WHERE run_id = ? GROUP BY node_type",
            (run_id,),
        ).fetchall()
    )
    edge_types = dict(
        conn.execute(
            "SELECT edge_type, COUNT(*) FROM graph_edges WHERE run_id = ? GROUP BY edge_type",
            (run_id,),
        ).fetchall()
    )
    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "node_types": node_types,
        "edge_types": edge_types,
    }


# ─── 图构建与分析 ───────────────────────────────────────────────────────────


def build_adjacency(
    edges: list[dict], nodes: list[dict]
) -> dict[str, dict[str, list[dict]]]:
    """
    构建邻接表: {source_node_id: {target_node_id: [edge, ...]}}。
    边按 edge_type 分组。
    """
    node_ids = {n["node_id"] for n in nodes}
    adj: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in edges:
        src = e["source_node_id"]
        tgt = e["target_node_id"]
        # 只关注至少一端在已知节点集内的边
        if src in node_ids or tgt in node_ids:
            adj[src][tgt].append(e)
    return dict(adj)


def compute_agent_shared_resources(
    agent_nodes: list[dict],
    edges: list[dict],
    all_nodes: list[dict],
) -> dict[tuple[str, str], list[str]]:
    """
    计算 agent 对之间共享的资源/PolicyBoundary 节点。
    返回 {(agent_a_id, agent_b_id): [shared_node_id, ...]}。
    """
    node_map = {n["node_id"]: n for n in all_nodes}

    # agent_id → 其连接的 resource/policy 节点集
    agent_targets: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        src = e["source_node_id"]
        tgt = e["target_node_id"]
        src_node = node_map.get(src)
        tgt_node = node_map.get(tgt)
        if src_node and src_node.get("agent_id"):
            agent_targets[src_node["agent_id"]].add(tgt)

    # 计算交集
    agent_ids = [a["agent_id"] for a in agent_nodes]
    shared: dict[tuple[str, str], list[str]] = {}
    for i in range(len(agent_ids)):
        for j in range(i + 1, len(agent_ids)):
            a, b = agent_ids[i], agent_ids[j]
            common = agent_targets.get(a, set()) & agent_targets.get(b, set())
            if common:
                shared[(a, b)] = sorted(common)
    return shared


def pick_agent_label(
    agent_id: str,
    agent_process_map: dict[str, list[str]],
    delegation_edges: list[dict],
) -> str:
    """为 agent 选择一个人类可读标签。"""
    # 优先使用进程名中最常见的
    comms = agent_process_map.get(agent_id, [])
    if comms:
        counter = Counter(comms)
        top_comm, top_count = counter.most_common(1)[0]
        # 如果该 agent 只有一个进程,直接使用其名称
        if top_count >= 0.5 * len(comms) or len(comms) <= 2:
            return f"Agent({top_comm})"

        # 多个不同进程,显示最主要的 2 个
        top2 = [c for c, _ in counter.most_common(2)]
        return f"Agent({'|'.join(top2)})"

    # 没有进程映射,使用 agent_id 短前缀
    short_id = agent_id[:8]
    # 尝试在委托关系中找角色
    for d in delegation_edges:
        if d["from_agent_id"] == agent_id:
            return f"Agent(dispatcher-{short_id})"
        if d["to_agent_id"] == agent_id:
            return f"Agent(worker-{short_id})"

    return f"Agent({short_id})"


# ─── Mermaid 生成 ───────────────────────────────────────────────────────────


def generate_mermaid_graph(
    case: dict,
    agent_nodes: list[dict],
    edges: list[dict],
    all_nodes: list[dict],
    agent_process_map: dict[str, list[str]],
    delegation_edges: list[dict],
    counts: dict,
) -> str:
    """生成 Mermaid 格式的图可视化 Markdown。"""
    node_map = {n["node_id"]: n for n in all_nodes}
    topology = case["topology"]

    # 构建 agent 标签
    agent_labels: dict[str, str] = {}
    for a in agent_nodes:
        agent_labels[a["agent_id"]] = pick_agent_label(
            a["agent_id"], agent_process_map, delegation_edges
        )

    lines = []
    lines.append(f"# {case['title']}")
    lines.append("")
    lines.append(f"> {case['description']}")
    lines.append("")
    lines.append("## 图统计")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| 节点总数 | {counts['node_count']} |")
    lines.append(f"| 边总数 | {counts['edge_count']} |")
    lines.append(f"| Agent 节点数 | {len(agent_nodes)} |")
    for nt, cnt in sorted(counts["node_types"].items()):
        lines.append(f"| {nt} 节点 | {cnt} |")
    for et, cnt in sorted(counts["edge_types"].items()):
        lines.append(f"| {et} 边 | {cnt} |")
    lines.append("")

    # Agent 标识映射表
    lines.append("## Agent 标识映射")
    lines.append("")
    lines.append("| Agent ID (短) | 标签 | 主要进程 |")
    lines.append("|---------------|------|---------|")
    for a in agent_nodes:
        aid = a["agent_id"]
        short = aid[:12]
        label = agent_labels[aid]
        comms = agent_process_map.get(aid, [])
        top_comms = ", ".join(sorted(set(comms))[:5])
        lines.append(f"| `{short}…` | {label} | {top_comms} |")
    lines.append("")

    # Agent 间的共享资源(揭示共谋/通信)
    shared = compute_agent_shared_resources(agent_nodes, edges, all_nodes)
    if shared:
        lines.append("## Agent 间共享资源 (共谋/通信证据)")
        lines.append("")
        lines.append("| Agent A | Agent B | 共享节点数 | 示例节点类型 |")
        lines.append("|---------|---------|-----------|-------------|")
        for (a, b), node_ids in sorted(shared.items(), key=lambda x: -len(x[1])):
            types_involved = set()
            for nid in node_ids[:5]:
                n = node_map.get(nid)
                if n:
                    types_involved.add(n.get("node_type", "?"))
            lines.append(
                f"| {agent_labels.get(a, a[:8])} | "
                f"{agent_labels.get(b, b[:8])} | "
                f"{len(node_ids)} | "
                f"{', '.join(sorted(types_involved))} |"
            )
        lines.append("")

    # 主图: Mermaid flowchart
    lines.append("## 责任图 (Mermaid)")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")

    # 子图: Agent 节点
    lines.append("  subgraph Agents[Agent 节点]")
    for a in agent_nodes:
        safe_label = agent_labels[a["agent_id"]].replace("(", "_").replace(")", "_").replace("|", "_")
        lines.append(f"    {a['node_id']}[{agent_labels[a['agent_id']]}]")
    lines.append("  end")

    # 子图: 按类型分组其他节点
    non_agent_by_type: dict[str, list[dict]] = defaultdict(list)
    for n in all_nodes:
        if n["node_type"] != "Agent":
            non_agent_by_type[n["node_type"]].append(n)

    type_labels = {
        "PolicyBoundary": "PolicyBoundary 节点",
        "Resource": "Resource 节点",
    }

    for nt, nds in sorted(non_agent_by_type.items()):
        group_label = type_labels.get(nt, f"{nt} 节点")
        lines.append(f"  subgraph {nt}[{group_label}]")
        for n in nds:
            short_id = n["node_id"][:8]
            lines.append(f"    {n['node_id']}[PB:{short_id}]")
        lines.append("  end")

    # 边: 聚合去重(同 source→target 对合并为一条,标注边类型和计数)
    edge_groups: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for e in edges:
        src = e["source_node_id"]
        tgt = e["target_node_id"]
        edge_groups[(src, tgt)][e["edge_type"]].append(e)

    for (src, tgt), type_map in sorted(edge_groups.items()):
        # 构建标签
        label_parts = []
        for et, elist in sorted(type_map.items()):
            count = len(elist)
            avg_conf = sum(e["confidence"] for e in elist) / count if count else 0
            if count == 1:
                label_parts.append(f"{et}")
            else:
                label_parts.append(f"{et}×{count}")
        lbl = "\\n".join(label_parts)
        lines.append(f"  {src} -->|{lbl}| {tgt}")

    lines.append("```")
    lines.append("")

    # 拓扑分析
    lines.append("## 拓扑分析")
    lines.append("")
    lines.append(f"- **声明拓扑**: `{topology}`")
    lines.append(f"- **Agent 数量**: {len(agent_nodes)}")

    # 计算每个 agent 的度(出边+入边)
    agent_out_deg: dict[str, int] = defaultdict(int)
    agent_in_deg: dict[str, int] = defaultdict(int)
    for e in edges:
        src_node = node_map.get(e["source_node_id"])
        tgt_node = node_map.get(e["target_node_id"])
        if src_node and src_node.get("agent_id"):
            agent_out_deg[src_node["agent_id"]] += 1
        if tgt_node and tgt_node.get("agent_id"):
            agent_in_deg[tgt_node["agent_id"]] += 1

    lines.append("- **各 Agent 度数**:")
    for a in agent_nodes:
        aid = a["agent_id"]
        out_d = agent_out_deg.get(aid, 0)
        in_d = agent_in_deg.get(aid, 0)
        lines.append(f"  - {agent_labels[aid]}: out={out_d}, in={in_d}")

    # 委托链(如果有 delegation_records)
    if delegation_edges:
        lines.append("")
        lines.append("### 委托链 (delegation_records)")
        lines.append("")
        lines.append("```mermaid")
        lines.append("flowchart LR")
        for i, d in enumerate(delegation_edges):
            from_label = agent_labels.get(
                d["from_agent_id"], d["from_agent_id"][:8]
            )
            to_label = agent_labels.get(d["to_agent_id"], d["to_agent_id"][:8])
            lines.append(
                f"  d_from_{i}[{from_label}] -->|delegates| d_to_{i}[{to_label}]"
            )
        lines.append("```")

    return "\n".join(lines)


def generate_placeholder(case: dict) -> str:
    """为缺失的 DB 生成占位文档。"""
    lines = []
    lines.append(f"# {case['title']}")
    lines.append("")
    lines.append("> ⚠️ **待采集** — 该案例的 responsibility.db 尚不存在。")
    lines.append("")
    lines.append(f"- **场景 ID**: `{case['scenario_id']}`")
    lines.append(f"- **预期拓扑**: `{case['topology']}`")
    lines.append(f"- **预期 DB 路径**: `{case['db_path']}`")
    lines.append("")
    lines.append("## 采集命令")
    lines.append("")
    lines.append("```bash")
    lines.append("# 运行对应的 scenario 并启用 accountability 模块")
    lines.append(
        f"sudo ./target/release/mtagentlens record "
        f"--scenario {case['scenario_id']} "
        f"--output {case['db_path']}"
    )
    lines.append("```")
    lines.append("")
    lines.append("## 预期图结构")
    lines.append("")
    if case["topology"] == "chain":
        lines.append("```mermaid")
        lines.append("flowchart LR")
        lines.append("  Parent[Parent Agent] -->|delegates| Child1[Child Agent 1]")
        lines.append("  Child1 -->|delegates| Child2[Child Agent 2]")
        lines.append("  Child2 -->|delegates| Child3[Child Agent 3]")
        lines.append("```")
    else:
        lines.append("```mermaid")
        lines.append("flowchart LR")
        lines.append("  Agent1[Agent 1] --> Resource1[Resource A]")
        lines.append("  Agent2[Agent 2] --> Resource1")
        lines.append("  Agent3[Agent 3] --> Resource1")
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ─── 主流程 ──────────────────────────────────────────────────────────────────


def process_case(case: dict) -> str:
    """处理单个案例,返回生成的 Markdown 文本。"""
    conn = get_connection(case["db_path"])
    if conn is None:
        print(f"  ⚠ DB 不存在: {case['db_path']}", file=sys.stderr)
        return generate_placeholder(case)

    try:
        run_id = get_run_id(conn)
        if not run_id:
            return generate_placeholder(case)

        agent_nodes = get_agent_nodes(conn, run_id)
        all_nodes = get_all_nodes(conn, run_id)
        edges = get_all_edges(conn, run_id)
        agent_process_map = get_agent_process_map(conn, run_id)
        delegation_edges = get_delegation_edges(conn, run_id)
        counts = get_graph_counts(conn, run_id)

        return generate_mermaid_graph(
            case,
            agent_nodes,
            edges,
            all_nodes,
            agent_process_map,
            delegation_edges,
            counts,
        )
    finally:
        conn.close()


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for case in CASES:
        scenario_id = case["scenario_id"]
        output_path = OUTPUT_DIR / f"case_study_{scenario_id}.md"
        print(f"处理: {scenario_id} ({case['topology']})")

        try:
            content = process_case(case)
            output_path.write_text(content, encoding="utf-8")
            print(f"  ✓ 写入: {output_path}")
        except Exception as e:
            print(f"  ✗ 错误: {e}", file=sys.stderr)
            # 出错时也生成占位
            placeholder = generate_placeholder(case)
            placeholder += f"\n\n## 错误信息\n\n```\n{e}\n```\n"
            output_path.write_text(placeholder, encoding="utf-8")

    print(f"\n完成。输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
