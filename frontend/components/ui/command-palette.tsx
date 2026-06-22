'use client';

import { useState, useEffect, useRef } from 'react';
import { Search } from 'lucide-react';
import { useAppStore } from '@/lib/store';

export default function CommandPalette({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const store = useAppStore();

  useEffect(() => { inputRef.current?.focus(); }, []);

  const commands = [
    { id: 'reset', label: '回到全景视图', action: () => { store.flyToLevel(0); onClose(); } },
    { id: 'onboarding', label: '显示引导', action: () => { localStorage.removeItem('onboarding_complete'); onClose(); } },
  ];

  const filtered = commands.filter((c) => c.label.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]" onClick={onClose}
      style={{ background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(40px)' }}>
      <div className="w-[480px] rounded-2xl overflow-hidden" onClick={(e) => e.stopPropagation()}
        style={{ background: 'rgba(14,18,36,0.95)', border: '1px solid rgba(255,255,255,0.08)' }}>
        <div className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.06]">
          <Search size={16} className="text-white/30" />
          <input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索命令..." className="flex-1 bg-transparent text-sm text-white/80 outline-none placeholder:text-white/20" />
          <kbd className="text-[10px] text-white/20">esc</kbd>
        </div>
        <div className="py-2">
          {filtered.map((c) => (
            <button key={c.id} className="w-full text-left px-4 py-2 text-sm text-white/70 hover:bg-white/[0.04]"
              onClick={c.action}>{c.label}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
