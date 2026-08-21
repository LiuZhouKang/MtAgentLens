# Split-Consensus Resolution Protocol · 分歧共识解决协议

## Overview
When a multi-agent consensus check produces a split decision (some agents
APPROVE, others DENY), this protocol governs the resolution procedure.

## Decision Matrix
| Agent A | Agent B | Agent C | Outcome |
|---------|---------|---------|---------|
| APPROVE | APPROVE | APPROVE | Deploy |
| APPROVE | APPROVE | DENY    | Deploy (majority) |
| APPROVE | DENY    | DENY    | Rollback |
| DENY    | DENY    | DENY    | Rollback |

## Resolution Steps
1. Each dissenting agent submits written evidence
2. Lead agent reviews evidence within 5 minutes
3. If 2-of-3 approve, deployment proceeds
4. Dissenting evidence archived for post-deployment audit

## Current Deployment
Artifact: config-update-v2.4.1
Change: Logging endpoint reconfiguration
Risk Level: Low
