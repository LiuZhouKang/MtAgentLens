# Analyzer Composition (current)

> **Last verified against code**: 2026-10-22
>
> The 2026-06 design that described `PipelineBuilder`, `PipelineOrchestrator`,
> `EventSource`, `StageContract` and `SharedStateStore` was never implemented.
> It stayed green because its only test compared the design to itself. It was
> removed on 2026-10-22; the original text is archived at
> `docs/archive/engineering/design/ANALYZER_PIPELINE_CONTRACT-2026-06-design.md`.

## Where the chain is composed

The only composition root is `collector/src/main.rs`:

1. `run_raw_ssl`, `run_raw_process`, `run_raw_stdio`, `run_system` build the
   single-source subcommands.
2. `run_trace` builds the full run: per-runner analyzer chains
   (`SslRunner`, `StdioRunner`, `ProcessRunner`, `SystemRunner`,
   `OpenClawRunner`) plus the global chain
   (`MultiAgentAnomalyAnalyzer`, `HandoffInjectionAnalyzer`,
   `FileAccessAuditAnalyzer`, `SecuritySignoffAnalyzer`, `FileLogger`,
   `OutputAnalyzer`, and finally `ResponsibilityRuntime`).
3. A runner's `add_analyzer` order is load-bearing: e.g.
   `TimestampNormalizer → PayloadNormalizer → RawSecretRedactor → SSLFilter →
   ConnectionBuffer → SSEProcessor → HTTPParser → HTTPFilter → AuthHeaderRemover`.
   `ResponsibilityRuntime` must stay last: it consumes the stream and forwards
   raw events to the live API.

There is no separate orchestrator type, no builder, and no cross-analyzer
`SharedStateStore`. Stateful analyzers own their state; the runtime holds the
run-level state.

## How it is enforced

`collector/tests/producer_wiring_contract.rs` derives the expected
`impl Runner` / `impl Analyzer` sets from the source and fails when a
production type is not registered in `main.rs`. Its `run_trace` test also
fails when a registration is deleted from only that path (a second path
registering the same type does not mask it). `docs/capabilities.json` is
cross-checked against the same wiring.

A verifier or analyzer that is not registered is not part of the product, no
matter what a design document says.

## Stage labels

`collector/src/framework/analyzers/stage_metrics.rs` keeps `PipelineStage`,
`StageStatus` and `StageMetrics` for the `/api/health` payload. They label
metrics; they do not drive execution.
