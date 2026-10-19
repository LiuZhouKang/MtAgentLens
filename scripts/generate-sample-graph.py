#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Generate TypeScript module from exported sample-run.json."""
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

def main():
    data = json.load(open(PROJECT / "frontend/public/data/sample-run.json"))

    nodes = data["graph"]["nodes"]
    edges = data["graph"]["edges"]
    metrics = data["metrics"]

    lines = [
        "// Auto-generated from env/runs/ real MtAgentLens database.",
        f"// {len(nodes)} nodes, {len(edges)} edges, {data['alerts']} alerts.",
        "// Regenerate: python3 scripts/generate-sample-graph.py",
        "",
        "import type { ResponsibilityNode, ResponsibilityEdge, GraphMetrics } from './types';",
        "",
        "export const sampleNodes: ResponsibilityNode[] =",
        "  " + json.dumps(nodes, ensure_ascii=False) + ";",
        "",
        "export const sampleEdges: ResponsibilityEdge[] =",
        "  " + json.dumps(edges, ensure_ascii=False) + ";",
        "",
        "export const sampleMetrics: GraphMetrics =",
        "  " + json.dumps(metrics, ensure_ascii=False) + ";",
        "",
    ]

    with open(PROJECT / "frontend/lib/sample-graph.ts", "w") as f:
        f.write("\n".join(lines))

    size_kb = len("\n".join(lines)) / 1024
    print(f"Generated frontend/lib/sample-graph.ts ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
