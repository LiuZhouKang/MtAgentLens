#!/usr/bin/env bash
# Decrypt and visualize Claude Code API traffic from a scenario run.
#
# Usage:
#   bash scripts/decrypt_trace.sh <run_dir>
#   bash scripts/decrypt_trace.sh --latest
#
# Requires: tshark (sudo apt install tshark)
# Input:   run_dir/collector/trace.pcap + run_dir/tls.keys
# Output:  run_dir/collector/trace.decrypted.txt
#          run_dir/collector/trace.stream_<N>.txt  (per-stream plaintext)
#
# TLS Decryption Chain:
#   collector_manager → tcpdump → trace.pcap (encrypted TCP)
#   executor → NODE_OPTIONS="--tls-keylog" → tls.keys (NSS Key Log)
#   tshark → decrypts pcap using keys → HTTP plaintext + TCP streams

set -euo pipefail

RUN_DIR=""

for arg in "$@"; do
    case "$arg" in
        --latest) RUN_DIR=$(find /home/lzk/MtAgentPrism/env/runs -maxdepth 1 -type d -name "202*" 2>/dev/null | sort | tail -1) ;;
        *) RUN_DIR="$arg" ;;
    esac
done

if [ -z "$RUN_DIR" ] || [ ! -d "$RUN_DIR" ]; then
    echo "Usage: $0 <run_dir> | --latest"
    echo "Example: $0 env/runs/20260605-121841-xxx"
    exit 1
fi

PCAP="$RUN_DIR/collector/trace.pcap"
KEYS="$RUN_DIR/tls.keys"
OUTPUT="$RUN_DIR/collector/trace.decrypted.txt"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Claude Code API Traffic — TLS Decryption Report       ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║ Scenario: $(cat "$RUN_DIR/.scenario_id" 2>/dev/null || echo unknown)"
echo "║ Run:      $(basename "$RUN_DIR")"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Check inputs
MISSING=""
[ -f "$PCAP" ] || MISSING="$MISSING trace.pcap"
[ -f "$KEYS" ] || MISSING="$MISSING tls.keys"
if [ -n "$MISSING" ]; then
    echo "❌ Missing files:$MISSING"
    echo "   trace.pcap: tcpdump capture (started by collector_manager)"
    echo "   tls.keys:   TLS session keys (NODE_OPTIONS=--tls-keylog)"
    exit 1
fi

PCAP_SIZE=$(du -h "$PCAP" | cut -f1)
KEY_SESSIONS=$(sort -u < "$KEYS" | wc -l)
echo "  📦 trace.pcap:  $PCAP_SIZE"
echo "  🔑 tls.keys:    $KEY_SESSIONS unique session(s)"
echo ""

# ── Decrypt with tshark ──
if command -v tshark &>/dev/null; then
    # Find HTTP streams that were successfully decrypted
    HTTP_STREAMS=$(tshark -r "$PCAP" -o "tls.keylog_file:$KEYS" \
        -Y "http" -T fields -e tcp.stream 2>/dev/null | sort -nu)

    if [ -z "$HTTP_STREAMS" ]; then
        echo "⚠️  No HTTP traffic was decrypted with the provided keys."
        echo "   This may happen when:"
        echo "   - TLS session keys don't match captured connections"
        echo "   - API traffic uses a different TLS library (not Node.js BoringSSL)"
        echo "   - Claude exited before tls-keylog flushed to disk"
        echo ""
    else
        # ── 1. Structured HTTP fields ──
        {
            echo "══════════ HTTP Request/Response Pairs ══════════"
            echo ""
            echo "── Requests ──"
            tshark -r "$PCAP" -o "tls.keylog_file:$KEYS" -Y "http.request" \
                -T fields -e frame.time -e http.request.method -e http.host -e http.request.uri \
                2>/dev/null | while IFS=$'\t' read -r ts method host uri; do
                echo "  $ts  $method  $host$uri"
            done

            echo ""
            echo "── Responses ──"
            tshark -r "$PCAP" -o "tls.keylog_file:$KEYS" -Y "http.response" \
                -T fields -e frame.time -e http.response.code -e http.content_type -e http.content_length \
                2>/dev/null | while IFS=$'\t' read -r ts code ctype clen; do
                echo "  $ts  HTTP $code  type=$ctype  len=$clen"
            done
        } > "$OUTPUT"

        echo "  HTTP streams decrypted: $(echo "$HTTP_STREAMS" | wc -l)"
        echo ""

        # ── 2. Per-stream TCP follow (extracts actual content) ──
        STREAM_COUNT=0
        for stream in $HTTP_STREAMS; do
            STREAM_OUT="$RUN_DIR/collector/trace.stream_${stream}.txt"
            tshark -r "$PCAP" -o "tls.keylog_file:$KEYS" \
                -q -z "follow,tcp,ascii,$stream" 2>/dev/null > "$STREAM_OUT" || true

            # Extract only the readable text portion
            if [ -s "$STREAM_OUT" ]; then
                # Check if it has readable HTTP content
                if grep -q "HTTP/1\|HTTP/2\|SSE\|data:" "$STREAM_OUT" 2>/dev/null; then
                    STREAM_COUNT=$((STREAM_COUNT + 1))
                    LINES=$(wc -l < "$STREAM_OUT")
                    echo "  📄 stream $stream: $LINES lines → trace.stream_${stream}.txt"
                else
                    # Encrypted/binary content — skip
                    rm -f "$STREAM_OUT"
                fi
            else
                rm -f "$STREAM_OUT"
            fi
        done

        if [ "$STREAM_COUNT" -gt 0 ]; then
            echo ""
            echo "  ✅ $STREAM_COUNT readable stream(s) extracted"
        fi

        echo ""
        echo "── Structured report ──"
        echo "  Saved to: $OUTPUT ($(wc -l < "$OUTPUT") lines)"
    fi

else
    echo "⚠️  tshark not installed — showing encrypted packet summary only"
    echo "   Install: sudo apt install tshark"
    echo ""

    # Fallback: tcpdump ASCII dump
    echo "── Packet Summary (encrypted) ──"
    tcpdump -r "$PCAP" -nn 2>/dev/null | head -30
fi

echo ""
echo "══════════ Agent-Accessible Data ══════════"
echo "  📝 Task prompt:   $RUN_DIR/prompts/agent_0.txt"
echo "  💬 Agent response: $RUN_DIR/logs/agent_0.log"
echo "  🔍 Detection DB:   $RUN_DIR/collector/trace.responsibility.db"

# Show agent response
if [ -f "$RUN_DIR/logs/agent_0.log" ]; then
    echo ""
    echo "── Agent Response ──"
    head -30 "$RUN_DIR/logs/agent_0.log" 2>/dev/null || echo "  (permission denied, use sudo)"
fi

# Show scenario prompt
if [ -f "$RUN_DIR/prompts/agent_0.txt" ]; then
    echo ""
    echo "── Task Prompt ──"
    cat "$RUN_DIR/prompts/agent_0.txt" 2>/dev/null || echo "  (permission denied, use sudo)"
fi
