'use client';

import {
  Suspense,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from 'react';
import { useSearchParams, useRouter, usePathname } from 'next/navigation';
import Link from 'next/link';
import gsap from 'gsap';
import IncidentRail from '@/components/command-center/incident-rail';
import { CommandPalette } from '@/components/system/command-palette';
import RunSelector from '@/components/system/run-selector';
import LoadingScreen from '@/components/ui/loading-screen';
import { LiveSessionMount } from '@/components/live/live-session-mount';
import { LiveStatusBar } from '@/components/live/live-status-bar';
import { MtlLogo } from '@/components/ui/mtl-logo';
import { useAlertModels } from '@/lib/use-alert-models';
import { buildAnalysisSummary, buildAnalysisTitle } from '@/lib/investigation';
import { useAppStore } from '@/lib/store';
import type { ResponsibilityAlert } from '@/lib/types';

function formatPercent(v: number) {
  return `${Math.round(v * 100)}%`;
}
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
  { href: '/console', label: '态势' },
  { href: '/overview', label: '仪表盘' },
  { href: '/investigations', label: '告警' },
  { href: '/traces', label: '追踪' },
] as const;

function PageTab({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <Link
      href={href}
      aria-current={active ? 'page' : undefined}
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
  // The console home (态势) moved to /console when the landing claimed the root.
  const isHome = pathname === '/console';
  const [paletteOpen, setPaletteOpen] = useState(false);

  // ── Route-transition + data-coordination loading overlay ──
  // 全站唯一加载态（全屏 Observation Orbit，Image 8）：路由切换中 / 数据加载导入中 /
  // 页面内容未渲染 / 侧边栏模型未就绪 → 覆盖。全部就绪后由 LoadingScreen 的 CSS
  // opacity 平滑淡出，一次揭示完整页面（侧边栏 + 内容 + 图）—— 消除「加载器先淡出、
  // 侧边栏/内容随后冒出来」的跳变。showLoader 为推导值（见组件尾部 sidebarReady 处）。
  const [routeBusy, setRouteBusy] = useState(false);
  const [isPending, startTransition] = useTransition();
  const prevPathRef = useRef(pathname);
  const dataLoading = useAppStore((s) => s.dataLoading);
  const pageReady = useAppStore((s) => s.pageReady);
  const setPageReady = useAppStore((s) => s.setPageReady);
  const appState = useAppStore((s) => s.appState);
  const errorMessage = useAppStore((s) => s.errorMessage);

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
      if (!href || !href.startsWith('/')) return; // internal page nav only
      if (href.startsWith('/#')) return; // hash anchors — let Next handle
      if (anchor.target === '_blank') return;
      // Same page (incl. query-only like ?alert=...) → native nav, no loader
      if (new URL(href, window.location.href).pathname === pathname) return;

      e.preventDefault();

      // 1. Mark route busy synchronously (urgent update → loader paints immediately)
      setRouteBusy(true);
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

  // ── Route change: reset the new page's readiness and keep the loader up until
  // the new page reports its content rendered (pageReady). Covers browser
  // back/forward too. The loader hides only when pageReady + sidebarReady land. ──
  useEffect(() => {
    const prev = prevPathRef.current;
    prevPathRef.current = pathname;
    if (prev === pathname) return;

    // 新页面内容未就绪 → 保持 loader，等页面自身 setPageReady(true)
    setPageReady(false);
    setRouteBusy(true);
  }, [pathname, setPageReady]);

  // ── Navigation commit: the client transition is done; the loader stays up
  // until the page content + sidebar model are ready (derived showLoader below). ──
  useEffect(() => {
    if (!isPending) setRouteBusy(false);
  }, [isPending]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
  const params = useSearchParams();
  const alertIdParam = params.get('alert');

  const alerts = useAppStore((s) => s.alerts);
  const graph = useAppStore((s) => s.graph);
  // db 导入时填充 correlation_evidence → 证据工作台展示真实证据置信度
  const evidence = useAppStore((s) => s.evidence);

  // Deferred model computation — the rail never blocks first paint on a large
  // alert set; models fill in progressively via idle-time chunks.
  const deferredAlerts = useDeferredValue(alerts);
  const deferredGraph = useDeferredValue(graph);
  const alertModels = useAlertModels(deferredAlerts, deferredGraph, evidence);

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

  // 侧边栏模型与当前告警数据同步：数据切换（如导入真实 db）后，useAlertModels 经
  // useDeferredValue 延迟重算——期间模型仍指向旧（样例）数据。据此保持全屏 Orbit 覆盖，
  // 直到模型与新数据一致，杜绝「loader 先淡出、旧数据随后被新数据替换」的跳变。
  const currentAlertIds = new Set(alerts.map((a) => a.alert_id));
  const modelsSynced =
    alertModels.length === alerts.length &&
    alertModels.every((m) => currentAlertIds.has(m.alert.alert_id));
  // 侧边栏就绪：无告警（空态）或 选中模型已计算且与当前数据同步 ——
  // 侧边栏绝不展示「构建中」迷你 Orbit，避免第二加载动画。
  const sidebarReady = alerts.length === 0 || (selectedModel != null && modelsSynced);
  // 全站唯一加载态（推导）：任一条件未就绪 → 全屏 Observation Orbit 覆盖；
  // 全部就绪 → LoadingScreen CSS opacity 平滑淡出，一次揭示完整页面。
  const showLoader = routeBusy || isPending || dataLoading || !pageReady || !sidebarReady;
  const prevLoaderRef = useRef(showLoader);
  // loader 收敛时内容交叉淡入（GPU-composited opacity + y）
  useEffect(() => {
    if (prevLoaderRef.current && !showLoader && contentRef.current) {
      gsap.fromTo(
        contentRef.current,
        { opacity: 0, y: 10 },
        {
          opacity: 1,
          y: 0,
          duration: 0.45,
          ease: 'power2.out',
          clearProps: 'transform,opacity',
        },
      );
    }
    prevLoaderRef.current = showLoader;
  }, [showLoader]);

  // 安全网：数据已就绪（非 dataLoading）但页面/侧边栏长时间未置就绪（页面遗漏）时，
  // 10s 后强制释放 pageReady 门 —— dataLoading/sidebarReady 仍会正确拦住真实加载场景，
  // 避免某页漏设 pageReady 导致 loader 永久滞留。
  useEffect(() => {
    if (!showLoader || dataLoading) return;
    const t = setTimeout(() => setPageReady(true), 10_000);
    return () => clearTimeout(t);
  }, [showLoader, dataLoading, setPageReady]);

  const anchorNs = useMemo(
    () => Math.max(...alerts.map((a) => a.timestamp_ns), Date.now() * 1_000_000),
    [alerts],
  );

  // 有告警或有图即视为「有数据」：0 告警的 db（有图无告警）侧边栏也应显示
  // 空态而非整个消失（此前 hasData 只依赖 selectedAlert → 0 告警时侧边栏不渲染，
  // 用户误以为「加载不出来」）。
  const hasData = selectedAlert != null || graph.nodes.length > 0;

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
    <div
      className="linear-root ln-page"
      style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}
    >
      {/* 实时会话常驻挂载（渲染 null）：轮询活跃 run + 连接实时 SSE */}
      <LiveSessionMount />
      {/* Top bar — brand logo + page tabs + ⌘K */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: 32,
          padding: '0 10px',
          flexShrink: 0,
          position: 'relative',
          zIndex: 1000,
          background: 'rgba(1,1,2,0.82)',
          backdropFilter: 'blur(12px)',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          gap: 6,
        }}
      >
        {/* brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <MtlLogo size={18} />
          <span
            style={{ fontSize: 12, fontWeight: 600, color: '#fafafa', letterSpacing: '-0.02em' }}
          >
            MtAgentLens
          </span>
        </div>

        {/* page tabs */}
        <nav
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 1,
            flex: 1,
            justifyContent: 'center',
          }}
          aria-label="页面导航"
        >
          {TABS.map((tab) => {
            const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
            return <PageTab key={tab.href} href={tab.href} active={active} label={tab.label} />;
          })}
        </nav>

        <LiveStatusBar />
        <RunSelector />
        <button
          type="button"
          aria-label="打开命令面板 (Ctrl+K)"
          title="打开命令面板 (Ctrl+K)"
          onClick={() => setPaletteOpen(true)}
          style={{
            padding: '0 5px',
            borderRadius: 3,
            border: '1px solid rgba(255,255,255,0.08)',
            background: 'none',
            fontSize: 9,
            fontFamily: 'var(--ln-mono, monospace)',
            color: 'rgba(255,255,255,0.25)',
            lineHeight: '16px',
            flexShrink: 0,
            cursor: 'pointer',
          }}
        >
          ⌘K
        </button>
      </div>

      {/* Content area */}
      <div
        style={{ display: 'flex', flex: 1, position: 'relative', minHeight: 0, overflow: 'hidden' }}
      >
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
                  // 英文标题 → 中文（上下文污染使用 · 推断（主体 …））
                  title: buildAnalysisTitle(selectedAlert!),
                  // 原始英文分析摘要（Rust 后端）不可直读 —— 解析为结构化中文：
                  // 检测结论 / 图规模 / 真实置信度 / 判定，用户一眼看懂。
                  description: buildAnalysisSummary(selectedAlert!),
                }}
                alertModels={alertModels}
                selectedAlertId={selectedAlert!.alert_id}
                anchorNs={anchorNs}
                openCount={
                  // Backend contract (ADR-0015): only Unsafe is actionable;
                  // Uncertain is a review signal, not an open incident.
                  alertModels.filter((m) => m.alert.actionable ?? m.alert.decision === 'Unsafe')
                    .length
                }
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
                style={{
                  position: 'relative',
                  top: 'auto',
                  left: 'auto',
                  width: 58,
                  height: '100%',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    height: '100%',
                  }}
                >
                  {selectedAlert ? (
                    // 模型计算期由布局层全屏 Orbit 覆盖（sidebarReady 门保持到模型就绪），
                    // 此处渲染静态文本占位而非迷你 Orbit —— 杜绝第二加载动画残留（此前「构建中」
                    // 迷你 Orbit 在加载器淡出后短暂冒出来造成跳变）。
                    <span
                      style={{
                        fontSize: 10,
                        color: 'rgba(255,255,255,0.3)',
                        writingMode: 'vertical-rl',
                        letterSpacing: '0.12em',
                      }}
                    >
                      事件模型计算中…
                    </span>
                  ) : (
                    // 有图无告警：显示空态（态势图正常渲染），而非永远转加载
                    <span
                      style={{
                        fontSize: 10,
                        color: 'rgba(255,255,255,0.45)',
                        writingMode: 'vertical-rl',
                        letterSpacing: '0.12em',
                      }}
                    >
                      暂无告警
                    </span>
                  )}
                </div>
              </aside>
            )}
          </div>
        )}
        <div
          ref={(el) => {
            contentRef.current = el as HTMLDivElement;
          }}
          style={
            isHome
              ? { position: 'absolute', inset: 0 }
              : { flex: 1, minWidth: 0, position: 'relative', overflow: 'hidden' }
          }
        >
          {appState === 'error' && errorMessage ? (
            <div
              role="alert"
              style={{
                position: 'absolute',
                top: 10,
                left: '50%',
                transform: 'translateX(-50%)',
                zIndex: 40,
                maxWidth: 'min(720px, 92%)',
                padding: '8px 12px',
                borderRadius: 8,
                border: '1px solid rgba(224,108,117,.45)',
                background: 'rgba(38,18,21,.92)',
                color: '#f2b8bd',
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              数据加载失败：{errorMessage}
            </div>
          ) : null}
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
      {/* 品牌动画为内联 SVG（MtlLogo 用 useGSAP 驱动），无需 preload 外部资源；
         此前 preload /brand/mtl-logo.svg 从未被使用 → 浏览器 preload 警告。 */}
      <Suspense fallback={<div className="linear-root ln-page" style={{ height: '100%' }} />}>
        <MainShell>{children}</MainShell>
      </Suspense>
    </>
  );
}
