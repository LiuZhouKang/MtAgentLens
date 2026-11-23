# Evidence-bounded WAM reference oracle (research prototype)

This is a **dependency-free, tested specification oracle**, **not** a drop-in
replacement for `collector/src/responsibility/evidence/correlator.rs`.

Purpose: formalize a reproducible, conservative test target for future Rust
implementation of multi-agent cross-layer intent/action association. It does
not infer maliciousness, model intent, data exfiltration, or unique responsibility.

## Usage

From the MtAgentLens repository root:

```bash
python3 -m unittest discover -s research/wam_reference/tests -v
python3 -m research.wam_reference.cli --example
python3 -m research.wam_reference.cli --input /path/to/synthetic-witnesses.json
```

## Critical semantics

- `MUST`: **one** uncontested, observed, precise dispatch bridge from a
  separately validated trusted producer (`trusted_ingest=True` set only at
  verified ingestion), with observed, healthy endpoints.
- `MAY`: matching process generation, witnessed lineage, structured native
  call reference, or unverified dispatch hint; **never** unique attribution.
- `NO`: only a separate observed trusted exclusion for an exact pair, *not*
  absence of a matching event. Absence does not imply negative evidence.
- `INDETERMINATE`: no supported candidate or mutually inconsistent observed
  evidence. The action-level result never invents a unique owner.

This uses *verified producer attestations as input assumptions*. Untrusted
text cannot create a bridge simply by claiming to originate from a trusted
producer. The production system must validate it at ingestion. The JSON CLI
**never accepts** `trusted_ingest` from a JSON fixture; therefore its output
will never claim a MUST merely from a caller-supplied string.

`rank` is a deterministic interpretability/tie-break tuple, **not** a
calibrated confidence, Bayesian posterior or ground truth. Duplicate evidence
IDs and reordering cannot increase scores. Numeric ranks cannot promote MAY to
MUST. Parent paths must use full `(pid, start_time_ns, namespace)` keys;
missing namespace, source tier and capture health default to UNKNOWN, not healthy.

## Scope and non-goals

- The oracle enumerates Cartesian pairs and is not streaming-optimized.
- It does not replace passive eBPF capture with application-code SDK hooks.
  Trusted dispatch attestations are optional; unavailable sources yield MAY or
  abstention. Its correctness is conditional on the attestations actually being
  trustworthy, and the reference tests do **not** establish that fact.
- A shared TLS socket and a tool call ID are *not* authority anchors.
- A successful `open()` is not proof of bytes read; a successful `connect()`
  is not proof of sensitive data exfiltration.
- These synthetic tests do not establish real-world accuracy, FPR, TPR or
  runtime overhead. Production deployment requires separately frozen runs.

## Promotion gates

1. Source-level trust boundary + negative tests for forged producer claims.
2. Rust implementation and equivalence fixtures against this oracle.
3. Stable streaming-vs-batch outputs, deterministic event permutation tests.
4. Empirical `MUST` precision, MAY candidate recall and abstention rate on
   independently annotated shared-process, PID-reuse and shared-TLS cases.
5. CPU/memory growth measurement against realistic high-volume event counts.
6. Only then wire the Rust path behind an opt-in experimental switch and
   run `./scripts/verify.sh` and Linux/eBPF integration tests.
