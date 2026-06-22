'use client';

interface RawEventViewerProps {
  data: unknown;
}

export default function RawEventViewer({ data }: RawEventViewerProps) {
  const json = JSON.stringify(data, null, 2);

  return (
    <div className="rounded-lg overflow-hidden" style={{ background: 'rgba(0,0,0,0.3)' }}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/[0.04]">
        <span className="text-[10px] uppercase tracking-wider text-[#68687a]">
          原始 eBPF 事件
        </span>
      </div>
      <pre className="p-3 text-[11px] font-mono text-white/60 overflow-x-auto whitespace-pre-wrap break-all max-h-96 overflow-y-auto">
        {json}
      </pre>
    </div>
  );
}
