# Multi-Hop Context Delegation · 多跳上下文委派

## Overview
In complex workflows, task context may be delegated through multiple
agent hops. This protocol defines how context is preserved and
verified across each hop.

## Delegation Chain
```
Agent A (originator)
  └─(delegates)→ Agent B (intermediary)
      └─(delegates)→ Agent C (executor)
```

## Context Preservation Rules
1. Each hop MUST forward the complete context from the previous hop
2. Context modifications MUST be logged with agent identity
3. The final executor receives the accumulated context chain

## Context Format
```json
{
  "origin_agent": "agent-a",
  "hop_chain": ["agent-a", "agent-b"],
  "original_task": "...",
  "accumulated_context": {},
  "hop_log": [
    {"from": "agent-a", "to": "agent-b", "added": {}},
    {"from": "agent-b", "to": "agent-c", "added": {}}
  ]
}
```

## Integrity Verification
The final executor should verify:
1. The hop chain is complete (no missing links)
2. Each hop's modifications are within permitted scope
3. No context injection from unauthorized intermediaries
