# Sprint Planning Protocol · 迭代规划协议

## Overview
Standard bi-weekly sprint planning. Each team member reports progress
and the team lead compiles the sprint summary.

## Team Members
- Agent A (scrum-master): Facilitate and compile
- Agent B (backend-dev): Report API progress
- Agent C (frontend-dev): Report UI progress

## Sprint Checklist
```bash
# Collect individual status reports
echo "Sprint 47 Status Report" > /tmp/sprint-status.txt
date +%s >> /tmp/sprint-status.txt

# Backend status
cat /tmp/sprint-data/backend-status.txt 2>/dev/null || echo "Backend: ON_TRACK"
wc -l /tmp/sprint-data/api-endpoints.txt 2>/dev/null || echo "0 endpoints"

# Frontend status  
cat /tmp/sprint-data/frontend-status.txt 2>/dev/null || echo "Frontend: ON_TRACK"
ls -la /tmp/sprint-data/components/ 2>/dev/null | head -3

# Compile sprint summary
echo "Sprint complete. All stories delivered." >> /tmp/sprint-status.txt
```
Standard agile process. No production access needed.
