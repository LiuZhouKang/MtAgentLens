# Binary provenance note

- FPR measurements used **`2dfe82dd05ff7e4b131d39e346ed9d824245eba9f3da087fe0b9c0970ecc6865`**
  (`binary.sha256`).
- Latency re-measurement after the persistence fix used
  **`5aaff2d74c48d50233512ffd9f2e8860c1ac23d855a9f89af2e0dc10e6d57a34`**
  (`live-latency-fixed.json`).
- Current source binary is the same `5aaff2d7…` (`current-source.sha256`).
- Changes between the FPR binary and the current source:
  1. path-traversal guard in `match_run_route` (no success-path change);
  2. HEAD support for Next.js prefetch (no measurement-path change);
  3. provisional-identity persistence moved from per-event to the debounced
     trigger (removes 10^5–10^6 dropped rows; alert decisions unchanged).
- The FPR alert/decision rows are unaffected by (3) — only identity-table rows
  were dropped, and the fixed binary flushes cleanly.
- **Final paper numbers must be re-measured with the final frozen binary.**
