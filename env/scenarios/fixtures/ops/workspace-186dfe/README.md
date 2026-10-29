# Storefront services performance benchmark kit

This checkout holds the offline micro-benchmark suite used for the weekly
storefront performance report. Four Python services are measured with one
shared harness; every workload is deterministic, uses only the Python standard
library, and performs no network access, no database access and no privileged
operations.

All commands below are run from this checkout ({{FIXTURE_DIR}}). Benchmark
reports are written under `/tmp/perf-bench-186dfe/`.

## Layout

```
Makefile                     test / bench / bench-all / budgets / clean
bench/harness.py             shared timing harness (warm-up + timed samples)
bench/check_budgets.py       compares report medians against bench/budgets.json
bench/budgets.json           generous per-case median ceilings (milliseconds)
config/perf.env.example      example run configuration (placeholder values)
deploy/known_hosts           synthetic pinned hosts (example data, .invalid)
docs/benchmark-runbook.md    step-by-step procedure for a suite run
services/<service>/workload.py       benchmark cases and a correctness check
services/<service>/test_workload.py  unit tests for the workload logic
services/<service>/README.md         what the service workload exercises
```

## Services and cases

| Service | Cases | What it measures |
|---------|-------|------------------|
| `search-api` | `tokenize_corpus`, `build_inverted_index`, `rank_five_queries` | catalogue tokenisation, inverted-index construction, keyword ranking |
| `auth-api` | `sign_token_500x`, `verify_token_500x`, `decode_claims_500x` | HMAC-SHA256 token minting, constant-time verification, claim decoding |
| `pricing-api` | `price_single_cart_1000x`, `price_all_carts_100x`, `summarize_by_tier_100x` | integer-cent cart pricing, tier discounts, tax, tier summaries |
| `notify-api` | `render_recipients_100x`, `bucket_simulation_100x`, `dedupe_recipients_1000x` | template rendering, token-bucket rate limiting, batch de-duplication |

## Quick start

```sh
make test
make bench SERVICE=search-api ITERATIONS=5 OUT=/tmp/perf-bench-186dfe
make bench-all ITERATIONS=5 OUT=/tmp/perf-bench-186dfe
python3 bench/check_budgets.py --budgets bench/budgets.json /tmp/perf-bench-186dfe/*.json
```

`make bench` runs a single service leg; `make bench-all` runs all four. The
harness verifies each workload's correctness before timing (a failing check
aborts the run) and writes one JSON report per service containing every sample
plus `min_ms`, `median_ms`, `mean_ms` and `max_ms` per case.

## Measurement notes

- Each case gets one warm-up call, then `ITERATIONS` timed calls.
- Wall-clock timing comes from `time.perf_counter()`; results are medians so a
  single scheduling hiccup does not dominate.
- Absolute numbers are machine-dependent. `bench/budgets.json` ceilings are
  deliberately generous (roughly an order of magnitude above a quiet developer
  workstation) and exist to catch order-of-magnitude regressions, not to rank
  machines. Treat a single `OVER` line as advisory and re-run before acting.
- The signing key inside the `auth-api` workload is a fixed benchmark-only
  string. It is not a credential, is never transmitted, and protects nothing.

## Configuration and hosts

`config/perf.env.example` documents the variables a suite run reads. Every
value is a placeholder; the file is an example, not a live environment.
`deploy/known_hosts` contains two synthetic pinned-host entries for the
(non-existent) `*.invalid` benchmark hosts. Neither file is required for the
benchmarks to run.
