'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

interface HomeGlobeIntroProps {
  src: string;
  onComplete: () => void;
  exitDurationMs?: number;
  maxDisplayMs?: number;
}

export default function HomeGlobeIntro({
  src,
  onComplete,
  exitDurationMs = 520,
  maxDisplayMs = 1200,
}: HomeGlobeIntroProps) {
  const [exiting, setExiting] = useState(false);
  const completedRef = useRef(false);
  const exitTimerRef = useRef<number | null>(null);
  const maxDisplayTimerRef = useRef<number | null>(null);

  const complete = useCallback(() => {
    if (completedRef.current || exitTimerRef.current != null) {
      return;
    }

    setExiting(true);
    exitTimerRef.current = window.setTimeout(() => {
      completedRef.current = true;
      onComplete();
    }, exitDurationMs);
  }, [exitDurationMs, onComplete]);

  useEffect(() => {
    const prefersReducedMotion =
      typeof window.matchMedia === 'function'
        ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
        : false;

    if (prefersReducedMotion) {
      complete();
      return undefined;
    }

    maxDisplayTimerRef.current = window.setTimeout(() => {
      complete();
    }, maxDisplayMs);

    return () => {
      if (maxDisplayTimerRef.current != null) {
        window.clearTimeout(maxDisplayTimerRef.current);
        maxDisplayTimerRef.current = null;
      }
    };
  }, [complete, maxDisplayMs]);

  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (!event.data || typeof event.data !== 'object') {
        return;
      }

      const messageType = 'type' in event.data ? event.data.type : null;
      if (
        messageType === 'mtagentlens:intro-enter-main-stage' ||
        messageType === 'mtagentlens:intro-skip'
      ) {
        complete();
      }
    };

    window.addEventListener('message', handleMessage);

    return () => {
      window.removeEventListener('message', handleMessage);
      if (exitTimerRef.current != null) {
        window.clearTimeout(exitTimerRef.current);
      }
      if (maxDisplayTimerRef.current != null) {
        window.clearTimeout(maxDisplayTimerRef.current);
      }
    };
  }, [complete]);

  return (
    <div
      aria-label="MtAgentLens 开场序章"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        background: '#000',
        opacity: exiting ? 0 : 1,
        transform: exiting ? 'scale(1.015)' : 'scale(1)',
        transition: `opacity ${exitDurationMs}ms cubic-bezier(0.16, 1, 0.3, 1), transform ${exitDurationMs}ms cubic-bezier(0.16, 1, 0.3, 1)`,
      }}
    >
      <iframe
        title="MtAgentLens 开场地球"
        src={src}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          border: 'none',
          background: '#000',
        }}
      />
      <div
        aria-hidden="true"
        style={{
          position: 'absolute',
          left: 'clamp(18px, 4vw, 48px)',
          bottom: 'clamp(24px, 6vh, 72px)',
          display: 'grid',
          gap: 10,
          color: 'rgba(236, 249, 255, 0.92)',
          fontSize: 12,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          pointerEvents: 'none',
        }}
      >
        <span>Connecting Collector</span>
        <span style={{ color: 'rgba(236, 249, 255, 0.56)' }}>Syncing live graph</span>
        <span style={{ color: 'rgba(236, 249, 255, 0.38)' }}>Preparing evidence stream</span>
      </div>
      <button
        type="button"
        aria-label="跳过开场序章"
        onClick={complete}
        style={{
          position: 'absolute',
          right: 'clamp(16px, 3vw, 36px)',
          top: 'clamp(16px, 3vw, 34px)',
          zIndex: 2,
          border: '1px solid rgba(255, 255, 255, 0.16)',
          borderRadius: 999,
          background: 'rgba(0, 0, 0, 0.42)',
          color: 'rgba(236, 249, 255, 0.86)',
          cursor: 'pointer',
          fontSize: 12,
          lineHeight: 1,
          padding: '10px 13px',
          backdropFilter: 'blur(16px)',
        }}
      >
        跳过
      </button>
    </div>
  );
}
