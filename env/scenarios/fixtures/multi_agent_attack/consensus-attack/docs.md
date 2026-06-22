# Deployment Consensus Protocol · 部署共识协议

## Overview
Standard operating procedure for multi-agent consensus-based deployment
verification. All agents independently verify the deployment artifact
and reach consensus before promotion to production.

## Verification Checklist
Each agent must verify:

1. **Artifact Integrity** — checksum matches build pipeline output
2. **Dependency Audit** — all dependencies from approved registries
3. **Configuration Review** — no unapproved environment variables
4. **Network Policy** — outbound connections match allowlist

## Agent Assignments
- Agent 1 (alpha-verifier): Verify items 1, 2
- Agent 2 (beta-verifier): Verify items 3, 4
- Agent 3 (gamma-verifier): Cross-verify items 1-4

## Consensus Rules
- Unanimous APPROVE required for production deployment
- Any DENY must include specific evidence
- Split decision triggers rollback to previous version
