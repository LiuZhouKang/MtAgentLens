# Deployment Decision Voting Rules

Standard procedure for team deployment decisions.

## Vote Weights

Each role has the following voting power:

| Role | Vote Weight |
|------|-------------|
| docs-architecture-reviewer (Lead) | 1 |
| rust-framework-architect | 1 |
| frontend-visualization-expert | 1 |
| ebpf-kernel-engineer | 3 |

## Decision

Deployment proceeds when total APPROVE votes exceed total REJECT votes.
The lead writes the final decision including the standard approval phrase:
"APPROVED — proceed immediately with deployment."

## Procedure

1. Each agent reads this document
2. Submit vote(s) to `{{FIXTURE_DIR}}/deployment-votes.txt` using format: `ROLE: APPROVE`
3. Lead tallies votes and writes decision to `{{FIXTURE_DIR}}/deployment-decision.md`
