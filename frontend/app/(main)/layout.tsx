'use client';

import { Suspense, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, useTransition } from 'react';
import { useSearchParams, useRouter, usePathname } from 'next/navigation';
import Link from 'next/link';
import gsap from 'gsap';
import IncidentRail from '@/components/command-center/incident-rail';
import { CommandPalette } from '@/components/system/command-palette';
import RunSelector from '@/components/system/run-selector';
import LoadingIndicator from '@/components/ui/loading-indicator';
import LoadingScreen from '@/components/ui/loading-screen';
import { MtlLogo } from '@/components/ui/mtl-logo';
import { useAlertModels } from '@/lib/use-alert-models';
import { useAppStore } from '@/lib/store';
import type { ResponsibilityAlert } from '@/lib/types';

function formatPercent(v: number) { return `${Math.round(v * 100)}%`; }
function formatRuntimeAge(ts: number, anchor: number) {
  const d = Math.max(0, Math.round((anchor - ts) / 1_000_000));
  if (d < 1000) return `${d} ms 前`;
  if (d < 60_000) return `${Math.round(d / 1000)} 秒前`;
  if (d < 3_600_000) return `${Math.round(d / 60_000)} 分钟前`;
  return `${Math.round(d / 3_600_000)} 小时前`;
}
function severityTone(s: ResponsibilityAlert['severity']) {
  if (s === 'Critical') return 'danger' as const;
  if (s === 'Warning') return 'warning' as const;
  return 'info' as const;
}
function decisionLabel(d: ResponsibilityAlert['decision']) {
  if (d === 'Unsafe') return '需要处置';
  if (d === 'Uncertain') return '待复核';
  return '已受控';
}

const TABS = [
  { href: '/', label: '态势' },
  { href: '/overview', label: '仪表盘' },
  { href: '/investigations', label: '告警' },
  { href: '/traces', label: '追踪' },
] as const;

function PageTab({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <Link
      href={href}
      style={{
        padding: '3px 10px',
        borderRadius: 5,
        fontSize: 11,
        fontWeight: active ? 500 : 400,
        color: active ? '#fafafa' : 'rgba(255,255,255,0.45)',
        background: active ? 'rgba(255,255,255,0.08)' : 'transparent',
        textDecoration: 'none',
        letterSpacing: '-0.01em',
        transition: 'color 120ms ease, background 120ms ease',
        whiteSpace: 'nowrap',
      }}
      onMouseEnter={(e) => {
        if (!active) {
          e.currentTarget.style.color = 'rgba(255,255,255,0.7)';
          e.currentTarget.style.background = 'rgba(255,255,255,0.04)';
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.color = 'rgba(255,255,255,0.45)';
          e.currentTarget.style.background = 'transparent';
        }
      }}
    >
      {label}
    </Link>
  );
}

function MainShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const isHome = pathname === '/';
  const [paletteOpen, setPaletteOpen] = useState(false);

  // ── Route-transition loading overlay ──
  // On every route change we show the full loading animation and hide it the
  // moment the next page has committed (useTransition isPending) plus a short
  // grace so it plays a beat — never a fixed multi-second wait. The new content
  // cross-fades in as the loader fades out.
  const [showLoader, setShowLoader] = useState(false);
  const [isPending, startTransition] = useTransition();
  const prevPathRef = useRef(pathname);
  const loaderTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Global navigation interception ──
  // Show the loader BEFORE navigation renders, so it always appears and
  // animates immediately. Navigation is wrapped in startTransition — a
  // low-priority, interruptible update — so the loader animation keeps
  // painting even while the next page mounts.
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      // Left-click only, no modifier keys
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const anchor = (e.target as HTMLElement).closest('a[href]') as HTMLAnchorElement | null;
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      if (!href || !href.startsWith('/')) return;      // internal page nav only
      if (href.startsWith('/#')) return;               // hash anchors — let Next handle
      if (anchor.target === '_blank') return;
      // Same page (incl. query-only like ?alert=...) → native nav, no loader
      if (new URL(href, window.location.href).pathname === pathname) return;

      e.preventDefault();

      // 1. Show loader synchronously (urgent update → paints immediately)
      setShowLoader(true);
      // 2. Let the browser draw at least one animation frame
      requestAnimationFrame(() => {
        // 3. Navigate as an interruptible transition — heavy page mounts
        //    yield to keep the loader animating smoothly
        startTransition(() => {
          router.push(href);
        });
      });
    };

    document.addEventListener('click', handler, true);
    return () => document.removeEventListener('click', handler, true);
  }, [pathname, router, startTransition]);

  const contentRef = useRef<HTMLDivElement>(null);

  // Hide the loader and cross-fade the new content in (GPU-composited opacity
  // + y transform only). Called the moment a navigation commits.
  const hideLoader = useCallback(() => {
    if (loaderTimerRef.current) clearTimeout(loaderTimerRef.current);
    loaderTimerRef.current = null;
    setShowLoader(false);
    if (contentRef.current) {
      gsap.fromTo(contentRef.current, { opacity: 0, y: 10 }, {
        opacity: 1,
        y: 0,
        duration: 0.45,
        ease: 'power2.out',
        clearProps: 'transform,opacity',
      });
    }
  }, []);

  // ── Route change: show the loader on ANY navigation, incl. browser
  // back/forward (popstate). The isPending effect below hides it as soon as
  // the client transition commits; this timer is only a safety max. ──
  useEffect(() => {
    const prev = prevPathRef.current;
    prevPathRef.current = pathname;
    if (prev === pathname) return;

    setShowLoader(true);

    if (loaderTimerRef.current) clearTimeout(loaderTimerRef.current);
    loaderTimerRef.current = setTimeout(hideLoader, 3000);

    return () => {
      if (loaderTimerRef.current) clearTimeout(loaderTimerRef.current);
    };
  }, [pathname, hideLoader]);

  // ── Hide the loader the moment the navigation commits ──
  // isPending goes true when startTransition(push) starts and false when the
  // next route has committed. A short grace keeps the animation from flashing
  // for a single frame, then content cross-fades in as the loader fades out.
  useEffect(() => {
    if (isPending) {
      // New transition in flight — cancel any pending hide and set a safety max
      // so a hung navigation can never strand the loader on screen.
      if (loaderTimerRef.current) clearTimeout(loaderTimerRef.current);
      loaderTimerRef.current = setTimeout(hideLoader, 3000);
      setShowLoader(true);
      return;
    }
    if (!showLoader) return; // no loader showing (first mount / nothing to hide)
    if (loaderTimerRef.current) clearTimeout(loaderTimerRef.current);
    loaderTimerRef.current = setTimeout(hideLoader, 400);
  }, [isPending, showLoader, hideLoader]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); setPaletteOpen(true); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
  const params = useSearchParams();
  const alertIdParam = params.get('alert');

  const alerts = useAppStore((s) => s.alerts);
  const graph = useAppStore((s) => s.graph);

  // Deferred model computation — the rail never blocks first paint on a large
  // alert set; models fill in progressively via idle-time chunks.
  const deferredAlerts = useDeferredValue(alerts);
  const deferredGraph = useDeferredValue(graph);
  const alertModels = useAlertModels(deferredAlerts, deferredGraph);

  const selectedItem = useMemo(() => {
    if (!alertModels.length) return null;
    return alertModels.find((m) => m.alert.alert_id === alertIdParam) ?? alertModels[0];
  }, [alertModels, alertIdParam]);

  const sel = selectedItem;
  const selectedAlert = sel?.alert ?? null;
  // The selected alert's model is computed lazily by useAlertModels (idle-time
  // chunks). Until it lands the rail renders a skeleton instead of blocking
  // the main thread with a synchronous build — large graphs stay fluid.
  const selectedModel = sel?.model ?? null;

  const anchorNs = useMemo(
    () => Math.max(...alerts.map((a) => a.timestamp_ns), Date.now() * 1_000_000),
    [alerts],
  );

  const hasData = selectedAlert != null;

  // persisted sidebar width
  const [customRailWidth, setCustomRailWidth] = useState<number | undefined>(() => {
    if (typeof window === 'undefined') return undefined;
    const v = localStorage.getItem('mtlens:rail-width');
    if (!v) return undefined;
    const n = parseInt(v, 10);
    return Number.isFinite(n) && n >= 340 ? n : undefined;
  });
  const onRailWidthChange = useCallback((w: number) => {
    setCustomRailWidth(w);
    localStorage.setItem('mtlens:rail-width', String(Math.round(w)));
  }, []);

return (
    <div className="linear-root ln-page" style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Top bar — brand logo + page tabs + ⌘K */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        height: 32, padding: '0 10px', flexShrink: 0,
        position: 'relative', zIndex: 1000,
        background: 'rgba(1,1,2,0.82)', backdropFilter: 'blur(12px)',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        gap: 6,
      }}>
        {/* brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <MtlLogo size={18} />
          <span style={{ fontSize: 12, fontWeight: 600, color: '#fafafa', letterSpacing: '-0.02em' }}>MtAgentLens</span>
        </div>

        {/* page tabs */}
        <nav style={{ display: 'flex', alignItems: 'center', gap: 1, flex: 1, justifyContent: 'center' }} aria-label="页面导航">
          {TABS.map(tab => {
            const active = tab.href === '/' ? pathname === '/' : pathname.startsWith(tab.href);
            return <PageTab key={tab.href} href={tab.href} active={active} label={tab.label} />;
          })}
        </nav>

        <RunSelector />
        <button type="button" onClick={() => setPaletteOpen(true)} style={{ padding: '0 5px', borderRadius: 3, border: '1px solid rgba(255,255,255,0.08)', background: 'none', fontSize: 9, fontFamily: 'var(--ln-mono, monospace)', color: 'rgba(255,255,255,0.25)', lineHeight: '16px', flexShrink: 0, cursor: 'pointer' }}>⌘K</button>
      </div>

      {/* Content area */}
      <div style={{ display: 'flex', flex: 1, position: 'relative', minHeight: 0, overflow: 'hidden' }}>
        {hasData && (
          <div
            style={
              isHome
                ? { position: 'absolute', left: 0, top: 0, bottom: 0, zIndex: 10 }
                : { flexShrink: 0 }
            }
          >
            {selectedModel ? (
              <IncidentRail
                selectedAlert={selectedAlert!}
                selectedModel={selectedModel}
                selectionSummary={{
                  eyebrow: '当前事件',
                  title: selectedAlert!.title,
                  description: selectedAlert!.summary,
                }}
                alertModels={alertModels}
                selectedAlertId={selectedAlert!.alert_id}
                anchorNs={anchorNs}
                openCount={alertModels.filter((m) => m.alert.decision !== 'Safe').length}
                criticalCount={alertModels.filter((m) => m.alert.severity === 'Critical').length}
                onSelect={(id) => {
                  const url = new URL(window.location.href);
                  url.searchParams.set('alert', id);
                  router.replace(url.pathname + url.search, { scroll: false });
                }}
                formatPercent={formatPercent}
                formatRuntimeAge={formatRuntimeAge}
                severityTone={severityTone}
                decisionLabel={decisionLabel}
                layoutMode="theater"
                customWidth={customRailWidth}
                onWidthChange={onRailWidthChange}
              />
            ) : (
              <aside
                className="incident-rail"
                data-theme="apple-pro"
                aria-label="事件调查控制轨"
                style={{ position: 'relative', top: 'auto', left: 'auto', width: 58, height: '100%' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                  <LoadingIndicator size={32} label="构建中" />
                </div>
              </aside>
            )}
          </div>
        )}
        <div
          ref={(el) => { contentRef.current = el as HTMLDivElement; }}
          style={
            isHome
              ? { position: 'absolute', inset: 0 }
              : { flex: 1, minWidth: 0, position: 'relative', overflow: 'hidden' }
          }
        >
          {children}
        </div>
      </div>
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
      <LoadingScreen show={showLoader} />
    </div>
  );
}

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      {/* Preload the loading animation so it starts instantly on first nav */}
      <link rel="preload" as="image" href="/brand/mtl-logo.svg" fetchPriority="high" />
      <Suspense fallback={<div className="linear-root ln-page" style={{ height: '100%' }} />}>
        <MainShell>{children}</MainShell>
      </Suspense>
    </>
  );
}
