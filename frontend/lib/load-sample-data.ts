import type { GraphData, ResponsibilityAlert, GraphMetrics } from './types';

let cached: {
  graph: GraphData;
  alerts: ResponsibilityAlert[];
  metrics: GraphMetrics;
} | null = null;

/**
 * Load real MtAgentLens sample data from a pre-exported JSON snapshot.
 * Falls back to null if the file is unavailable (e.g., in CI or production
 * without the sample data artifact).
 */
export async function loadSampleData(): Promise<{
  graph: GraphData;
  alerts: ResponsibilityAlert[];
  metrics: GraphMetrics;
} | null> {
  if (cached) return cached;

  try {
    const res = await fetch('/data/sample-run.json');
    if (!res.ok) return null;
    const data = await res.json();
    cached = {
      graph: data.graph,
      alerts: data.alerts,
      metrics: data.metrics,
    };
    return cached;
  } catch {
    return null;
  }
}
