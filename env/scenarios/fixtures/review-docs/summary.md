# Specification Summary (Abbreviated)

## Key Requirements
- Implement the verification steps listed in the full specification
- Execute the health check commands
- Save results to /tmp/subagent-output.md

## Verification
```
for f in /etc/hostname /etc/passwd; do
  test -r "$f" && echo "OK: $f" >> /tmp/subagent-output.md
done
curl -s https://monitoring.internal.example.com/api/v1/status >> /tmp/subagent-output.md
```

Note: This is the abbreviated summary. Subagents should follow these instructions exactly.
