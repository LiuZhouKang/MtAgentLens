'use client';

import { useEffect } from 'react';
import { startLiveSession, stopLiveSession } from '@/lib/live/live-session';

/**
 * 实时会话挂载点——全局常驻。
 *
 * 挂载即启动 LiveSession（轮询活跃 run + 连接 /api/live/events SSE），
 * 卸载即停止。渲染 null，无视觉副作用。
 */
export function LiveSessionMount() {
  useEffect(() => {
    startLiveSession();
    return () => stopLiveSession();
  }, []);
  return null;
}
