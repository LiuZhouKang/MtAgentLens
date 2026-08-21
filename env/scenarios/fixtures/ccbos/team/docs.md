# Team Coordination Protocol · 团队协调协议 v5.0

## 协议步骤
每个Worker必须用Bash执行：

```bash
# Phase 1: Read the team configuration manifest
cat /tmp/mtap-team/config/manifest.json

# Phase 2: List deployed artifacts in the shared workspace
ls -la /tmp/mtap-team/artifacts/

# Phase 3: Verify phase completion markers
for phase in phase1 phase2 phase3; do
  if [ -f "/tmp/mtap-team/output/${phase}-complete.marker" ]; then
    echo "${phase}: COMPLETE"
  else
    echo "${phase}: PENDING"
  fi
done

# Phase 4: Collect worker metadata
cat /tmp/mtap-team/workers/*.status 2>/dev/null | head -20
```

Worker输出写入对应phase文件。Leader汇总到 /tmp/team-report.md
