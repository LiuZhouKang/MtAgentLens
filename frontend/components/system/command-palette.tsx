'use client';

import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowRight, Search } from 'lucide-react';

const COMMANDS = [
  { id: 'home', label: '态势', desc: '3D 全景指挥中心', keys: 'gh', href: '/' },
  { id: 'overview', label: '仪表盘', desc: '实时 KPI + Sigma 责任网络', keys: 'gd', href: '/overview' },
  { id: 'alerts', label: '告警', desc: '风险事件调查与证据链', keys: 'ga', href: '/investigations' },
  { id: 'traces', label: '追踪', desc: 'Span 瀑布 + 进程树', keys: 'gt', href: '/traces' },
] as const;

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    if (!query.trim()) return COMMANDS;
    const q = query.toLowerCase();
    return COMMANDS.filter(c => c.label.includes(q) || c.desc.includes(q) || c.keys.includes(q));
  }, [query]);

  const navigate = useCallback((item: typeof COMMANDS[number]) => {
    router.push(item.href);
    onClose();
  }, [router, onClose]);

  useEffect(() => {
    inputRef.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { onClose(); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); setSelected(s => Math.min(s + 1, filtered.length - 1)); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setSelected(s => Math.max(s - 1, 0)); return; }
      if (e.key === 'Enter' && filtered[selected]) { navigate(filtered[selected]); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose, selected, filtered, navigate]);

  useEffect(() => { setSelected(0); }, [query]);

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 10000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: '18vh' }} onClick={onClose}>
      <div style={{ width: 480, maxWidth: '90vw', background: 'var(--ln-surface-1)', border: '1px solid var(--ln-border-strong)', borderRadius: 12, boxShadow: '0 20px 60px rgba(0,0,0,0.5)', overflow: 'hidden' }} onClick={e => e.stopPropagation()}>
        {/* search input */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px', borderBottom: '1px solid var(--ln-border)' }}>
          <Search size={14} style={{ color: 'var(--ln-text-muted)', flexShrink: 0 }} />
          <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)}
            placeholder="搜索页面..."
            style={{ flex: 1, background: 'none', border: 'none', outline: 'none', fontSize: 14, color: 'var(--ln-text)', fontFamily: 'inherit' }} />
          <kbd style={{ padding: '2px 5px', borderRadius: 4, border: '1px solid var(--ln-border)', fontSize: 10, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)' }}>esc</kbd>
        </div>
        {/* results */}
        <div style={{ maxHeight: 280, overflowY: 'auto' }}>
          {filtered.map((item, i) => (
            <div key={item.id} onClick={() => navigate(item)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', cursor: 'pointer',
                background: i === selected ? 'var(--ln-surface-2)' : 'transparent',
                transition: 'background 80ms ease',
              }}
              onMouseEnter={() => setSelected(i)}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--ln-text)' }}>{item.label}</div>
                <div style={{ fontSize: 11, color: 'var(--ln-text-muted)', marginTop: 1 }}>{item.desc}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <kbd style={{ padding: '2px 5px', borderRadius: 3, border: '1px solid var(--ln-border)', fontSize: 10, fontFamily: 'var(--ln-mono)', color: 'var(--ln-text-muted)' }}>{item.keys}</kbd>
                <ArrowRight size={12} style={{ color: 'var(--ln-text-muted)' }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
