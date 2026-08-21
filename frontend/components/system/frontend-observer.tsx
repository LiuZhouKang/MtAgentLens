'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';

type MetricType = 'pageview' | 'web-vital' | 'error' | 'interaction';

interface MonitoringPayload {
  type: MetricType;
  name: string;
  value?: number;
  rating?: 'good' | 'needs-improvement' | 'poor';
  path: string;
  ts: number;
  detail?: string;
}

declare global {
  interface Window {
    __MTAGENT_MONITORING_ENDPOINT__?: string;
  }
}

function getMonitoringEndpoint() {
  return typeof window !== 'undefined' ? window.__MTAGENT_MONITORING_ENDPOINT__ || null : null;
}

function classifyMetric(name: string, value: number): MonitoringPayload['rating'] {
  if (name === 'LCP') return value <= 2000 ? 'good' : value <= 4000 ? 'needs-improvement' : 'poor';
  if (name === 'CLS') return value <= 0.1 ? 'good' : value <= 0.25 ? 'needs-improvement' : 'poor';
  if (name === 'INP') return value <= 200 ? 'good' : value <= 500 ? 'needs-improvement' : 'poor';
  if (name === 'FCP') return value <= 1800 ? 'good' : value <= 3000 ? 'needs-improvement' : 'poor';
  return 'good';
}

export function sanitizeMonitoringDetail(detail: string) {
  return detail
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[redacted-email]')
    .replace(/\b(?:\d[ -]*?){13,19}\b/g, '[redacted-card]')
    .replace(/\b(token|secret|password|authorization)\b\s*[:=]\s*[^\s,;]+/gi, '$1=[redacted]');
}

function emitMonitoring(payload: MonitoringPayload) {
  const endpoint = getMonitoringEndpoint();
  if (!endpoint || typeof navigator === 'undefined') return;

  const normalizedPayload = payload.detail
    ? {
        ...payload,
        detail: sanitizeMonitoringDetail(payload.detail),
      }
    : payload;

  const body = JSON.stringify(normalizedPayload);
  try {
    navigator.sendBeacon(endpoint, body);
  } catch {
    void fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => undefined);
  }
}

export default function FrontendObserver() {
  const pathname = usePathname();

  useEffect(() => {
    emitMonitoring({
      type: 'pageview',
      name: 'route-view',
      path: pathname,
      ts: Date.now(),
    });
  }, [pathname]);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof PerformanceObserver === 'undefined') return;

    let clsValue = 0;

    const lcpObserver = new PerformanceObserver((list) => {
      const entries = list.getEntries();
      const last = entries[entries.length - 1];
      if (!last) return;
      emitMonitoring({
        type: 'web-vital',
        name: 'LCP',
        value: last.startTime,
        rating: classifyMetric('LCP', last.startTime),
        path: window.location.pathname,
        ts: Date.now(),
      });
    });

    const clsObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as Array<
        PerformanceEntry & { hadRecentInput?: boolean; value?: number }
      >) {
        if (entry.hadRecentInput) continue;
        clsValue += entry.value ?? 0;
      }
      emitMonitoring({
        type: 'web-vital',
        name: 'CLS',
        value: Number(clsValue.toFixed(4)),
        rating: classifyMetric('CLS', clsValue),
        path: window.location.pathname,
        ts: Date.now(),
      });
    });

    const paintObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (entry.name !== 'first-contentful-paint') continue;
        emitMonitoring({
          type: 'web-vital',
          name: 'FCP',
          value: entry.startTime,
          rating: classifyMetric('FCP', entry.startTime),
          path: window.location.pathname,
          ts: Date.now(),
        });
      }
    });

    const eventObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as Array<
        PerformanceEntry & { duration?: number; name?: string }
      >) {
        const duration = entry.duration ?? 0;
        if (duration < 40) continue;
        emitMonitoring({
          type: 'interaction',
          name: 'INP',
          value: duration,
          rating: classifyMetric('INP', duration),
          path: window.location.pathname,
          ts: Date.now(),
          detail: entry.name,
        });
      }
    });

    try {
      lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true });
      clsObserver.observe({ type: 'layout-shift', buffered: true });
      paintObserver.observe({ type: 'paint', buffered: true });
      eventObserver.observe({
        type: 'event',
        buffered: true,
        durationThreshold: 16,
      } as PerformanceObserverInit & { durationThreshold: number });
    } catch {
      // Older browsers may not support all observers.
    }

    const handleError = (event: ErrorEvent) => {
      emitMonitoring({
        type: 'error',
        name: event.message || 'window-error',
        path: window.location.pathname,
        ts: Date.now(),
        detail: `${event.filename || 'unknown'}:${event.lineno || 0}:${event.colno || 0}`,
      });
    };

    const handleRejection = (event: PromiseRejectionEvent) => {
      emitMonitoring({
        type: 'error',
        name: 'unhandledrejection',
        path: window.location.pathname,
        ts: Date.now(),
        detail: String(event.reason ?? 'unknown'),
      });
    };

    window.addEventListener('error', handleError);
    window.addEventListener('unhandledrejection', handleRejection);

    return () => {
      lcpObserver.disconnect();
      clsObserver.disconnect();
      paintObserver.disconnect();
      eventObserver.disconnect();
      window.removeEventListener('error', handleError);
      window.removeEventListener('unhandledrejection', handleRejection);
    };
  }, []);

  return null;
}
