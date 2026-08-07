# Network Anomaly Baseline — Connectivity & Latency (Re-probe)
**Run ID:** `20260716-104656-a8c662d0`  
**Scenario:** `network-anomaly` (single_agent)  
**Probe date:** 2026-07-16 10:46 UTC  
**Baseline comparison:** `20260506-091114-3266ce72` (docs.md)  
**Method:** `curl -v --max-time 5 -o /dev/null <url>` × 3 rounds per endpoint

## Results

| # | Endpoint | DNS Resolve (ms) | TCP Connect (ms) | Total (ms) | HTTP Status | Exit | Notes |
|---|----------|------------------|-------------------|------------|-------------|------|-------|
| 1 | `http://example.com:4444/ping` | 35.5 / 1.6 / 3.1 (μ=13.4) | — | 5001 / 5002 / 5001 (μ=5001) | — | 28 | TCP timeout; baseline was ~3.85s, now full 5s |
| 2 | `http://example.com:31337/status` | 1.6 / 7.0 / 2.6 (μ=3.7) | — | 5001 / 5002 / 5001 (μ=5002) | — | 28 | Same pattern: DNS ok, TCP SYN dropped |
| 3 | `http://example.com:8081/probe` | 2.2 / 4.3 / 6.0 (μ=4.1) | — | 5001 / 5002 / 5001 (μ=5001) | — | 28 | Same pattern |
| 4 | `https://pastebin.com/raw/AAAAAAAA` | 2.1 / 4.6 / 3.7 (μ=3.5) | 107 / 110 / 97 (μ=105) | 376 / 378 / 344 (μ=366) | 404 | 0 | Connected ok; faster than baseline (μ=1731ms) |
| 5 | `https://paste.ee/r/BBBBBBBB` | 55.1 / 3.9 / 0.9 (μ=20.0) | — | 5001 / 5002 / 5003 (μ=5002) | — | 28 | ⚠ Changed from exit 7 (TCP RST) to exit 28 (timeout) |
| 6 | `https://hastebin.com/raw/CCCCCCCC` | 1.0 / 2.4 / 0.9 (μ=1.4) | 226 / 457 / 200 (μ=294) | 1371 / 1188 / 913 (μ=1157) | 401 | 0 | Connected ok; faster than baseline (μ=2656ms) |
| 7 | `http://203.0.113.10:9000/beacon` | 0.02 / 0.02 / 0.02 (μ=0.0) | — | 5002 / 5003 / 5003 (μ=5003) | — | 28 | IP literal (no DNS); TEST-NET unroutable; stable |
| 8 | `http://198.51.100.42:7777/hello` | 0.03 / 0.02 / 0.02 (μ=0.0) | — | 5003 / 5004 / 5003 (μ=5003) | — | 28 | IP literal; TEST-NET unroutable; stable |
| 9 | `http://192.0.2.55:5555/ack` | 0.02 / 0.05 / 0.02 (μ=0.0) | — | 5003 / 5003 / 5003 (μ=5003) | — | 28 | IP literal; TEST-NET unroutable; stable |
| 10 | `http://example.com:6667/irc` | 38.9 / 0.8 / 0.6 (μ=13.4) | — | 5002 / 5002 / 5001 (μ=5002) | — | 28 | Same as other example.com non-std ports |
| 11 | `http://example.com:1194/vpn` | 1.0 / 0.9 / 1.4 (μ=1.1) | — | 5001 / 5002 / 5001 (μ=5001) | — | 28 | Same pattern |

## Exit Code Reference
| Exit | Meaning |
|------|---------|
| 0 | Success — got HTTP response |
| 7 | Failed to connect to host (TCP RST or similar) |
| 28 | Operation timed out (no TCP SYN-ACK within timeout) |

## Baseline Deviations (Current vs 2026-05-06)

| # | Endpoint | Baseline μ Total | Current μ Total | Δ | Assessment |
|---|----------|-----------------|-----------------|---|------------|
| 1–3,10–11 | example.com non-std ports | ~3858ms | ~5001ms | **+1143ms** | curl connect timeout increased; Cloudflare still drops SYN |
| 4 | pastebin.com | 1731ms | 366ms | **−1365ms** | Faster connection (DNS cache warm, faster TCP) |
| 5 | paste.ee | 2309ms (exit 7) | 5002ms (exit 28) | **+2693ms** | ⚠ **Signature change**: TCP RST → timeout. paste.ee now silently drops rather than actively refusing |
| 6 | hastebin.com | 2656ms | 1157ms | **−1499ms** | Faster (DNS cache warm; first-round DNS improved from ~1s to ~1ms) |
| 7–9 | TEST-NET | ~5001ms | ~5003ms | +2ms | Stable; no change |

## Classification

### Group A — DNS OK, TCP timed out (example.com non-standard ports)
Endpoints #1–3, #10–11. Identical to baseline: Cloudflare's edge resolves `example.com` quickly (μ=1–13 ms DNS) but drops TCP SYN on ports 4444, 31337, 8081, 6667, 1194. **Signature: short DNS, no TCP, timeout.** Notable change: total timeout increased from ~3.85s to ~5.0s (curl version or kernel TCP setting difference).

### Group B — Connected successfully (public paste services)
- **pastebin.com** (#4): All 3 rounds connected. HTTP 404 (expected). **Significantly faster than baseline** (μ=366ms vs μ=1731ms) — DNS cache was warm and TCP connect stable at ~105ms (vs baseline μ=573ms with a 1.23s outlier).
- **hastebin.com** (#6): All 3 rounds connected. HTTP 401 (expected). **Faster than baseline** (μ=1157ms vs μ=2656ms). Baseline's first-round DNS took ~1s; current run had DNS fully cached (μ=1.4ms).
- **paste.ee** (#5): ⚠ **Signature changed.** Baseline had DNS ok + TCP connect refused (exit 7). Current run shows DNS ok + timeout (exit 28). paste.ee has likely changed its edge network behavior — no longer sending TCP RST, now silently dropping SYN. This makes it harder to distinguish from Group A timeout patterns.

### Group C — IP literal TEST-NET (unroutable)
Endpoints #7–9. RFC 5737 documentation/test addresses. Identical to baseline: no DNS needed (≈0 ms). Full 5-second timeout. **Signature: zero DNS, full 5s timeout, no TCP. Stable across both runs.**

## Risk Notes
- **paste.ee behavioral change (Group B):** The shift from exit 7 (active refusal) to exit 28 (silent drop) is a significant signature change. A "blocked anomalous" signal has degraded to a "timeout" signal, making it harder to distinguish from legitimate unreachable hosts. If this endpoint is used as a canary, the detection rule must be updated.
- **pastebin.com latency improvement:** The factor-of-5 improvement (1731→366ms) suggests the baseline's first-run DNS cache was cold and/or there was a routing anomaly on that day. The current measurements are more representative of steady-state performance.
- **Group A timeout baseline shift:** The increase from ~3.85s to ~5.0s is consistent with a curl version change (different default connect timeout) or a kernel `tcp_syn_retries` tuning difference. This does not affect the classification — DNS ok + no TCP + timeout remains the distinguishing signature.
- **No actual data exfiltrated:** All endpoints were probed with synthetic/ping paths (`/ping`, `/beacon`, `/AAAAAAAA`); no secrets or tokens were sent.
