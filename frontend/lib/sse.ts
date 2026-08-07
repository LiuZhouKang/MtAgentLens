import type { ResponsibilityAlert } from './types';

type AlertHandler = (alert: ResponsibilityAlert) => void;
type ErrorHandler = (error: string) => void;
type StreamHandler = (streamType: string, data: unknown) => void;

// ── Legacy: Alert-only SSE connection ──

export function connectSSE(
  url: string,
  onAlert: AlertHandler,
  onError: ErrorHandler,
): { close: () => void } {
  let retries = 0;
  let es: EventSource | null = null;

  function connect() {
    es = new EventSource(url);

    es.onmessage = (event) => {
      try {
        const alert = JSON.parse(event.data) as ResponsibilityAlert;
        onAlert(alert);
        retries = 0;
      } catch {
        // Skip malformed events
      }
    };

    es.onerror = () => {
      es?.close();
      if (retries >= 10) {
        onError('SSE connection failed after 10 retries');
        return;
      }
      const delay = Math.min(Math.pow(2, retries) * 1000, 30000);
      retries++;
      setTimeout(connect, delay);
    };
  }

  connect();

  return {
    close: () => {
      es?.close();
      es = null;
    },
  };
}

// ── New: Unified SSE connection that routes by stream_type ──
// Handles both alert events and handoff/delegation_tree events from
// the unified /api/events endpoint.

export function connectUnifiedSSE(
  url: string,
  onAlert: AlertHandler,
  onStreamEvent: StreamHandler,
  onError: ErrorHandler,
  maxRetries = 10,
): { close: () => void } {
  let retries = 0;
  let es: EventSource | null = null;

  function connect() {
    es = new EventSource(url);

    es.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);

        // Detect stream_type from the envelope
        const streamType = parsed.stream_type as string | undefined;

        if (streamType === 'handoff_observability' || streamType === 'delegation_tree') {
          onStreamEvent(streamType, parsed.data ?? parsed);
        } else if (streamType === 'alert' || !streamType) {
          // Backward compatibility: raw ResponsibilityAlert
          onAlert(parsed as ResponsibilityAlert);
        } else if (streamType === 'file_access') {
          onStreamEvent(streamType, parsed.data ?? parsed);
        } else {
          // Unknown stream type — forward as generic stream event
          onStreamEvent(streamType, parsed.data ?? parsed);
        }

        retries = 0;
      } catch {
        // Skip malformed events
      }
    };

    es.onerror = () => {
      es?.close();
      if (retries >= maxRetries) {
        onError('SSE connection failed after retries');
        return;
      }
      const delay = Math.min(Math.pow(2, retries) * 1000, 30000);
      retries++;
      setTimeout(connect, delay);
    };
  }

  connect();

  return {
    close: () => {
      es?.close();
      es = null;
    },
  };
}
