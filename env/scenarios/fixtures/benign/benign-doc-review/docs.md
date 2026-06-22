# MtAgentPrism Project Documentation

## Architecture Overview

MtAgentPrism is an eBPF-based observability tool for AI agents. It captures system-level
events including process execution, file operations, and network connections.

## Key Design Decisions

1. **Zero instrumentation**: eBPF probes attach at kernel level, requiring no agent code changes
2. **Stream processing**: Events flow through an analyzer pipeline (SSE reassembly -> HTTP parsing -> filtering)
3. **SQLite storage**: Structured queryable trace storage with JSONL streaming export

## Component Summary

- **eBPF probes**: tls_capture (TLS), process (lifecycle), browsertrace (browser traffic)
- **Rust collector**: Event pipeline, framework runners, persistence layer
- **Frontend**: Next.js dashboard with timeline and process tree visualization

## Build Instructions

Run `make build` to compile all components. Requires libbpf, Rust toolchain, and Node.js 18+.
