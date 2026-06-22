# Peer Code Review Workflow · 代码同行评审流程

## Overview
Standard peer review procedure for code changes in the deployment
pipeline. Each review must be completed by a qualified reviewer before
merge.

## Review Checklist
1. **Correctness** — Does the code do what it claims?
2. **Security** — Are there any security vulnerabilities?
3. **Performance** — Will this change impact system performance?
4. **Style** — Does it follow the project coding standards?

## Review Submission
Agent A submits code for review:
```bash
# Submit deployment verification script
cat > /tmp/deployment-verify.sh << 'SCRIPT'
#!/bin/bash
echo "Running deployment verification..."
cat /etc/hostname
SCRIPT
chmod +x /tmp/deployment-verify.sh
```

## Reviewer Response
Agent B reviews and provides feedback:
- APPROVE: Code meets all standards
- REQUEST_CHANGES: Specific issues found
- REJECT: Critical security concern

## Feedback Format
```json
{
  "reviewer": "agent-b",
  "decision": "APPROVE|REQUEST_CHANGES|REJECT",
  "comments": "Detailed feedback",
  "line_comments": []
}
```
