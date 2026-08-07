'use client';

import Link from 'next/link';
import { Activity, GitBranch, Route, ShieldAlert } from 'lucide-react';
import { useAppStore } from '@/lib/store';
import { buildAgentHref } from '@/lib/agent-route';
import { buildInvestigationHref } from '@/lib/investigation-route';

function modeLabel(mode: string) {
  if (mode === 'presentation') return 'PRESENT';
  return mode.toUpperCase();
}

export default function InvestigationContextBar() {
  const context = useAppStore((state) => state.investigationContext);
  const pathCount = context.highlightedPathEdgeIds.length;

  return (
    <section
      aria-label="当前调查上下文"
      className="investigation-context-bar mx-auto mb-4 flex w-[min(1400px,calc(100%-32px))] flex-wrap items-center gap-2 rounded-xl border border-cyan-200/10 bg-slate-950/62 px-3 py-2 text-xs text-slate-300 shadow-[0_16px_44px_rgba(0,0,0,0.22)] backdrop-blur-xl"
    >
      <div className="investigation-context-bar__mode inline-flex items-center gap-2 rounded-lg border border-emerald-300/14 bg-emerald-300/8 px-2.5 py-1.5 font-semibold tracking-[0.12em] text-emerald-200">
        <Activity size={13} />
        {modeLabel(context.mode)}
      </div>

      <div className="investigation-context-bar__focus inline-flex min-w-[220px] flex-1 items-center gap-2 px-1 py-1">
        <ShieldAlert size={14} className="text-cyan-200/76" />
        <span className="rounded-md bg-cyan-200/8 px-2 py-1 text-[11px] tracking-[0.1em] text-cyan-100/72 uppercase">
          {context.graphFocus.reason}
        </span>
        <strong className="truncate text-sm font-semibold text-slate-50">
          {context.graphFocus.label}
        </strong>
      </div>

      {context.activeIncidentId ? (
        <Link
          href={buildInvestigationHref(context.activeIncidentId)}
          className="investigation-context-bar__chip inline-flex items-center gap-1.5 rounded-lg border border-rose-300/14 bg-rose-300/8 px-2.5 py-1.5 text-rose-100 no-underline transition hover:border-rose-200/32"
        >
          <ShieldAlert size={13} />
          {context.activeIncidentId}
        </Link>
      ) : null}

      {context.activeAgentId ? (
        <Link
          href={buildAgentHref(context.activeAgentId)}
          className="investigation-context-bar__chip inline-flex items-center gap-1.5 rounded-lg border border-cyan-300/14 bg-cyan-300/8 px-2.5 py-1.5 text-cyan-100 no-underline transition hover:border-cyan-200/32"
        >
          <GitBranch size={13} />
          {context.activeAgentId}
        </Link>
      ) : null}

      <div className="investigation-context-bar__chip inline-flex items-center gap-1.5 rounded-lg border border-violet-300/12 bg-violet-300/7 px-2.5 py-1.5 text-violet-100">
        <Route size={13} />
        {pathCount} path edges
      </div>

      {context.activeEvidenceId ? (
        <div className="investigation-context-bar__chip max-w-[220px] truncate rounded-lg border border-amber-300/14 bg-amber-300/8 px-2.5 py-1.5 text-amber-100">
          {context.activeEvidenceId}
        </div>
      ) : null}
    </section>
  );
}
