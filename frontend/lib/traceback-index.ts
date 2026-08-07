import type { ResponsibilityTraceback } from './types';

/**
 * Reverse index over the real FK traceback.claim_id → claim. The collector
 * stores tracebacks keyed by their claim, while claims carry a mostly-NULL
 * traceback_ref — so lookups go through this index, never claim.traceback_ref.
 */
export function buildTracebacksByClaimId(
  tracebacks: ResponsibilityTraceback[],
): Map<string, ResponsibilityTraceback> {
  const index = new Map<string, ResponsibilityTraceback>();
  for (const tb of tracebacks) {
    // Same claim re-traced → last write wins (most recent traceback).
    index.set(tb.claim_id, tb);
  }
  return index;
}
