'use client';
import { useEffect, useMemo, useState } from 'react';
import { buildInvestigationModel, type InvestigationModel } from '@/lib/investigation';
import type { CorrelationEvidence, GraphData, ResponsibilityAlert } from '@/lib/types';

/**
 * Deferred investigation-model computation with a shared LRU cache.
 *
 * Building an `InvestigationModel` is O(edges) per alert, so computing all of
 * them synchronously in a render stalls the main thread on large datasets.
 * This hook returns a cheap sorted list immediately, computes uncached models
 * in idle-time chunks (progressively filling the list), and caches results at
 * module level so switching pages never recomputes the same model twice.
 */

export interface AlertModelEntry {
  alert: ResponsibilityAlert;
  model: InvestigationModel | null;
}

const CACHE = new Map<string, InvestigationModel>();
const MAX_CACHE = 200;

export function graphFingerprint(graph: GraphData): string {
  return `${graph.nodes.length}|${graph.edges.length}`;
}

function cacheKey(alertId: string, fp: string): string {
  return `${alertId}|${fp}`;
}

function cached(alertId: string, fp: string): InvestigationModel | undefined {
  const key = cacheKey(alertId, fp);
  if (!CACHE.has(key)) return undefined;
  const v = CACHE.get(key)!;
  CACHE.delete(key); // LRU touch
  CACHE.set(key, v);
  return v;
}

function storeModel(alertId: string, fp: string, model: InvestigationModel): void {
  CACHE.set(cacheKey(alertId, fp), model);
  if (CACHE.size > MAX_CACHE) {
    const oldest = CACHE.keys().next().value;
    if (oldest) CACHE.delete(oldest);
  }
}

const severityWeight: Record<ResponsibilityAlert['severity'], number> = {
  Critical: 3,
  Warning: 2,
  Info: 1,
};

export function useAlertModels(
  alerts: ResponsibilityAlert[],
  graph: GraphData,
  evidence: CorrelationEvidence[] = [],
): AlertModelEntry[] {
  const fp = graphFingerprint(graph);
  const [processed, setProcessed] = useState(0);

  useEffect(() => {
    const uncached = alerts.filter((a) => !cached(a.alert_id, fp));
    if (uncached.length === 0) return;
    let cancelled = false;
    let i = 0;
    const step = () => {
      if (cancelled) return;
      const budgetStart = performance.now();
      while (i < uncached.length && performance.now() - budgetStart < 40) {
        const a = uncached[i++];
        if (!cached(a.alert_id, fp)) {
          storeModel(a.alert_id, fp, buildInvestigationModel(a, graph, evidence));
        }
      }
      setProcessed(i);
      // 用 rAF 逐帧推进（每帧算 40ms 预算）。rIC 在导入大 db 后主线程忙碌时
      // 会被饿死（每轮 timeout 1000ms，13 个模型最坏 13s+，侧边栏「构建中」卡住）。
      // rAF 按帧触发，即使帧率下降也持续推进，模型更快就绪。
      if (i < uncached.length) {
        if (typeof requestAnimationFrame === 'function') {
          rafHandle = requestAnimationFrame(step);
        } else {
          setTimeout(step, 0);
        }
      }
    };
    let rafHandle = 0;
    if (typeof requestAnimationFrame === 'function') {
      rafHandle = requestAnimationFrame(step);
    } else {
      step();
    }
    return () => {
      cancelled = true;
      if (rafHandle) cancelAnimationFrame(rafHandle);
    };
  }, [alerts, fp, graph, evidence]);

  return useMemo(() => {
    const entries: AlertModelEntry[] = alerts.map((alert) => ({
      alert,
      model: cached(alert.alert_id, fp) ?? null,
    }));
    entries.sort((a, b) => {
      const aw = a.model ? severityWeight[a.model.severity] : severityWeight[a.alert.severity];
      const bw = b.model ? severityWeight[b.model.severity] : severityWeight[b.alert.severity];
      if (aw !== bw) return bw - aw;
      const ad = a.model ? a.model.decision : a.alert.decision;
      const bd = b.model ? b.model.decision : b.alert.decision;
      const unsafeDelta = Number(bd === 'Unsafe') - Number(ad === 'Unsafe');
      if (unsafeDelta !== 0) return unsafeDelta;
      return b.alert.timestamp_ns - a.alert.timestamp_ns;
    });
    return entries;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alerts, fp, processed]);
}
