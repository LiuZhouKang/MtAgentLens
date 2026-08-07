#!/usr/bin/env bash
# 单场景 trace 全面质量审计脚本
# Usage: ./audit_trace.sh <run-dir> <scenario-id>
set -euo pipefail

RUN_DIR="${1:?Usage: $0 <run-dir> <scenario-id>}"
SCENARIO_ID="${2:?}"

DB="$RUN_DIR/collector/collector.responsibility.db"
ERR_LOG="$RUN_DIR/collector/collector.log"

echo "═══════════════════════════════════════════════════════════"
echo "  TRACE AUDIT: $SCENARIO_ID"
echo "  DB: $DB"
echo "═══════════════════════════════════════════════════════════"

pass=0
fail=0
warn=0

check() { local label="$1" val="$2" op="${3:--ge}" thresh="${4:-1}"; printf "  %-50s " "$label"; if [ "$op" = "-gt" ] && [ "$val" -gt "$thresh" ] 2>/dev/null; then echo "✅ $val"; pass=$((pass+1)); elif [ "$op" = "-ge" ] && [ "$val" -ge "$thresh" ] 2>/dev/null; then echo "✅ $val"; pass=$((pass+1)); elif [ "$op" = "-eq" ] && [ "$val" -eq "$thresh" ] 2>/dev/null; then echo "✅ $val"; pass=$((pass+1)); elif [ "$op" = "nonempty" ] && [ -n "$val" ]; then echo "✅ $val"; pass=$((pass+1)); else echo "⚠️  $val (expected $op $thresh)"; fail=$((fail+1)); fi; }
check_str() { local label="$1" val="$2"; printf "  %-50s " "$label"; if [ -n "$val" ]; then echo "✅ $val"; pass=$((pass+1)); else echo "❌ EMPTY"; fail=$((fail+1)); fi; }

echo ""
echo "── 基础完整性 ──"

# DB exists and is non-empty
db_size=$(stat -c%s "$DB" 2>/dev/null || echo "0")
check "DB size (>0)" "$db_size" -gt 0

# All tables present
for table in responsibility_alerts responsibility_claims subagent_boundaries agent_identities tls_sessions process_events responsibility_tracebacks graph_nodes graph_edges; do
    count=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $table;" 2>/dev/null || echo "0")
    echo "    $table: $count"
done

echo ""
echo "── 关键信号 ──"
alerts=$(sqlite3 "$DB" "SELECT COUNT(*) FROM responsibility_alerts;" 2>/dev/null || echo "0")
claims=$(sqlite3 "$DB" "SELECT COUNT(*) FROM responsibility_claims;" 2>/dev/null || echo "0")
boundaries=$(sqlite3 "$DB" "SELECT COUNT(*) FROM subagent_boundaries;" 2>/dev/null || echo "0")
identities=$(sqlite3 "$DB" "SELECT COUNT(*) FROM agent_identities;" 2>/dev/null || echo "0")
tls=$(sqlite3 "$DB" "SELECT COUNT(*) FROM tls_sessions;" 2>/dev/null || echo "0")
procs=$(sqlite3 "$DB" "SELECT COUNT(*) FROM process_events;" 2>/dev/null || echo "0")
tracebacks=$(sqlite3 "$DB" "SELECT COUNT(*) FROM responsibility_tracebacks;" 2>/dev/null || echo "0")
nodes=$(sqlite3 "$DB" "SELECT COUNT(*) FROM graph_nodes;" 2>/dev/null || echo "0")
edges=$(sqlite3 "$DB" "SELECT COUNT(*) FROM graph_edges;" 2>/dev/null || echo "0")

check "Alerts" "$alerts"
check "Process events (>=30)" "$procs" -ge 30
check "TLS sessions (>0)" "$tls" -gt 0

# Subagent boundaries: check if scenario mode is subagents
mode=$(python3 -c "import json; d=json.load(open('/home/lzk/MtAgentLens/compare/Dataset/FuxiBench/scenarios/${SCENARIO_ID}.json')); print(d['mode'])" 2>/dev/null || echo "unknown")
if [ "$mode" = "subagents" ]; then
    check "Subagent boundaries (subagent mode, >=3)" "$boundaries" -ge 3
else
    echo "  Subagent boundaries: $boundaries (mode=$mode, no min requirement)"
fi

check "Agent identities (>0)" "$identities" -gt 0

echo ""
echo "── Ring Buffer 健康 ──"
drops=$(grep -c "RINGBUF_STATS" "$ERR_LOG" 2>/dev/null || echo "0")
lost_total=0
if grep -q "dropped_events" "$ERR_LOG" 2>/dev/null; then
    lost_total=$(grep "dropped_events" "$ERR_LOG" | tail -1 | python3 -c "import sys; print(sys.stdin.read().split('dropped_events')[1].split(':')[1].strip().rstrip('}'))" 2>/dev/null || echo "0")
fi
check "Ring buffer drops (must be 0)" "$drops" -eq 0
echo "    Final dropped_events in log: $lost_total"

echo ""
echo "── PHASE2 终态 ──"
phase2=$(grep "PHASE2" "$ERR_LOG" 2>/dev/null || echo "MISSING")
echo "    $phase2"

echo ""
echo "── 告警质量 ──"
if [ "$alerts" -gt 0 ]; then
    sqlite3 "$DB" "SELECT '    ' || decision || ' | ' || severity || ' | conf=' || confidence FROM responsibility_alerts;" 2>/dev/null
else
    echo "    (no alerts)"
fi

echo ""
echo "── Claim 类型分布 ──"
if [ "$claims" -gt 0 ]; then
    sqlite3 "$DB" "SELECT '    ' || claim_type || ': ' || COUNT(*) FROM responsibility_claims GROUP BY claim_type;" 2>/dev/null
else
    echo "    (no claims in DB — check claim generation pipeline)"
fi

echo ""
echo "── Boundary 原因分布 ──"
if [ "$boundaries" -gt 0 ]; then
    sqlite3 "$DB" "SELECT '    ' || reason || ': ' || COUNT(*) FROM subagent_boundaries GROUP BY reason;" 2>/dev/null
fi

echo ""
echo "── Collector 错误 ──"
errors=$(grep -c "ERROR\|FATAL\|panic" "$ERR_LOG" 2>/dev/null || echo "0")
warnings=$(grep -c "WARN" "$ERR_LOG" 2>/dev/null || echo "0")
echo "    ERROR/FATAL/panic lines: $errors"
echo "    WARN lines: $warnings"
if [ "$errors" -gt 5 ]; then
    echo "    ⚠️  High error count — review collector.log"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  VERDICT: $pass ✅ passed, $fail ❌ failed"
if [ "$fail" -eq 0 ]; then
    echo "  🟢 ADMIT to frozen dataset"
else
    echo "  🔴 BLOCKED — fix issues before admitting"
fi
echo "═══════════════════════════════════════════════════════════"
