'use client';

import { useAppStore } from '@/lib/store';
import { Play, Pause } from 'lucide-react';
import { useState } from 'react';

export default function TimelineScrubber() {
  const [playing, setPlaying] = useState(false);
  const position = useAppStore((s) => s.timelinePosition);
  const setPosition = useAppStore((s) => s.setTimelinePosition);

  return (
    <div
      className="absolute right-0 bottom-0 left-0 z-30 flex h-12 items-center gap-4 px-4"
      style={{
        background: 'rgba(8, 12, 24, 0.85)',
        backdropFilter: 'blur(20px)',
        borderTop: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      <button onClick={() => setPlaying(!playing)} className="text-white/60 hover:text-white/90">
        {playing ? <Pause size={16} /> : <Play size={16} />}
      </button>
      <input
        type="range"
        min={0}
        max={100}
        value={position}
        onChange={(e) => setPosition(Number(e.target.value))}
        className="h-1 flex-1 appearance-none rounded-full bg-white/[0.08] [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[#818cf8]"
      />
    </div>
  );
}
