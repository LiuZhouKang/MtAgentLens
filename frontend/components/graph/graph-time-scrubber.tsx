'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { motion, useReducedMotion } from 'motion/react';
import { useAppStore } from '@/lib/store';
import { buildFrameDiff, summarizeFrameChange } from '@/lib/snapshot-evolution';

const PLAY_INTERVAL_MS = 1200;

/** 相对 run 起点的紧凑时长文案：t+ms / t+s ms。 */
function formatRelativeTs(ts: number, base: number): string {
  const ms = Math.max(0, Math.round((ts - base) / 1_000_000));
  if (ms >= 1000) {
    const s = Math.floor(ms / 1000);
    return `t+${s}s ${ms % 1000}ms`;
  }
  return `t+${ms}ms`;
}

/**
 * 图时间演化滑杆——帧播放/步进/跳转 + 帧索引 + 相对时间 + delta 徽章。
 * 只驱动 store 的 snapshotFrameIndex；帧内容经 graphOverride 供 stage 渲染
 * （不改全局 store.graph，裁决 #6）。frames ≤ 1 时返回 null 自隐藏。
 */
export default function GraphTimeScrubber() {
  // 先选稳定的 dbAssets（zustand 引用相等），再派生 frames——若直接在 selector 里
  // `?? []` 会每次返回新数组引用，触发无限重渲染。
  const dbAssets = useAppStore((s) => s.dbAssets);
  const frames = dbAssets?.snapshotFrames ?? [];
  const index = useAppStore((s) => s.snapshotFrameIndex);
  const setIndex = useAppStore((s) => s.setSnapshotFrameIndex);
  const reduceMotion = useReducedMotion();

  const [playing, setPlaying] = useState(false);
  const len = frames.length;
  const clamped = len > 0 ? Math.min(Math.max(index, 0), len - 1) : 0;

  // 播放循环：定时器经 ref 读取最新帧索引，避免每次前进重建 interval。
  // ref 更新放在 effect 中（react-hooks/refs 禁止 render 期写 ref）。
  const indexRef = useRef(clamped);
  useEffect(() => {
    indexRef.current = clamped;
  }, [clamped]);

  useEffect(() => {
    if (playing && len <= 1) setPlaying(false);
  }, [len, playing]);

  useEffect(() => {
    if (!playing || len <= 1) return;
    const timer = setInterval(() => {
      setIndex((indexRef.current + 1) % len); // 帧边界循环
    }, PLAY_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [playing, len, setIndex]);

  const step = useCallback(
    (dir: number) => {
      if (len <= 1) return;
      setIndex(Math.min(Math.max(clamped + dir, 0), len - 1));
    },
    [clamped, len, setIndex],
  );

  if (len <= 1) return null;

  const frame = frames[clamped];
  const delta = clamped > 0 ? summarizeFrameChange(buildFrameDiff(frames[clamped - 1], frame)) : '—';

  return (
    <div
      role="group"
      aria-label="时间演化"
      className="pointer-events-auto fixed bottom-5 left-1/2 z-[60] -translate-x-1/2"
    >
      <div className="flex items-center gap-4 rounded-2xl border border-white/10 bg-black/50 px-5 py-3 shadow-2xl backdrop-blur-xl">
        <span className="font-mono text-[11px] tabular-nums text-white/70">
          {clamped + 1} / {len}
        </span>

        <div className="relative h-1 w-56 rounded-full bg-white/10">
          <motion.div
            className="absolute top-0 h-1 rounded-full bg-gradient-to-r from-indigo-400/80 to-cyan-400/80"
            style={{ width: `${(clamped / (len - 1)) * 100}%` }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            aria-hidden="true"
          />
          {frames.map((f, i) => {
            const pct = (i / (len - 1)) * 100;
            const active = i === clamped;
            return (
              <button
                key={f.snapshot_id}
                type="button"
                aria-label={`帧 ${i + 1}：${f.snapshot_id}`}
                title={f.snapshot_id}
                onClick={() => setIndex(i)}
                className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full"
                style={{ left: `${pct}%` }}
              >
                <motion.span
                  className="block rounded-full"
                  style={{
                    width: active ? 10 : 7,
                    height: active ? 10 : 7,
                    background: active ? '#7dd3fc' : 'rgba(255,255,255,0.35)',
                  }}
                  animate={active && !reduceMotion ? { scale: [1, 1.4, 1] } : undefined}
                  transition={active && !reduceMotion ? { duration: 1.2, repeat: Infinity } : undefined}
                />
              </button>
            );
          })}
        </div>

        <button
          type="button"
          onClick={() => setPlaying((p) => !p)}
          aria-label={playing ? '暂停播放' : '播放演化'}
          className="flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/[0.06] text-[10px] text-white/80 transition-all hover:bg-white/[0.12] hover:text-white"
        >
          {playing ? '❚❚' : '▶'}
        </button>
        <button
          type="button"
          onClick={() => step(-1)}
          aria-label="上一帧"
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/5 bg-white/[0.02] text-[10px] text-white/60 transition-all hover:bg-white/[0.08] hover:text-white"
        >
          ⏮
        </button>
        <button
          type="button"
          onClick={() => step(1)}
          aria-label="下一帧"
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/5 bg-white/[0.02] text-[10px] text-white/60 transition-all hover:bg-white/[0.08] hover:text-white"
        >
          ⏭
        </button>

        <div className="flex flex-col gap-0.5">
          <span className="font-mono text-[10px] tabular-nums text-white/50">
            {formatRelativeTs(frame.timestamp_ns, frames[0].timestamp_ns)}
          </span>
          <span className="font-mono text-[10px] tabular-nums text-cyan-300/80">{delta}</span>
        </div>
      </div>
    </div>
  );
}
