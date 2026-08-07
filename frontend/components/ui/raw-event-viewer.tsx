'use client';

interface RawEventViewerProps {
  data: unknown;
}

export default function RawEventViewer({ data }: RawEventViewerProps) {
  const json = JSON.stringify(data, null, 2);

  return (
    <div className="overflow-hidden rounded-lg" style={{ background: 'rgba(0,0,0,0.3)' }}>
      <div className="flex items-center justify-between border-b border-white/[0.04] px-3 py-1.5">
        <span className="text-[10px] tracking-wider text-[#68687a] uppercase">原始 eBPF 事件</span>
      </div>
      <pre className="max-h-96 overflow-x-auto overflow-y-auto p-3 font-mono text-[11px] break-all whitespace-pre-wrap text-white/60">
        {json}
      </pre>
    </div>
  );
}
