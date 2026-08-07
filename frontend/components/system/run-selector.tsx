'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppStore } from '@/lib/store';
import type { DataSourceMeta } from '@/lib/store';
import { api } from '@/lib/api';
import type { SnapshotImport } from '@/lib/snapshot-import';
import { parseSnapshotJson } from '@/lib/snapshot-import';
import type { DbImport } from '@/lib/db-import';
import { loadDatabase as loadDatabaseFromBytes } from '@/lib/db-import';
import {
  Check,
  ChevronDown,
  Database,
  FileJson,
  FlaskConical,
  Loader2,
  Search,
  Sparkles,
  Upload,
} from 'lucide-react';

/** Run summary as served by the collector's /api/runs endpoint. */
type RunInfo = {
  run_id: string;
  scenario_ids?: string[];
  run_status?: string;
  artifact_availability?: {
    alerts?: boolean;
    correlation_evidence?: boolean;
    events?: boolean;
    evaluation?: boolean;
    /** A real SQLite artifact (collector.responsibility.db) is present. */
    database?: boolean;
  };
};

/** A locally imported file — either a JSON snapshot or a SQLite db. */
type LocalImport = SnapshotImport | DbImport;

function isDbImport(value: LocalImport): value is DbImport {
  return 'evidence' in value;
}

function runLabel(run: RunInfo): string {
  return run.scenario_ids?.[0] ?? run.run_id.slice(0, 12);
}

/** A run is loadable if it has any graph/alerts/events/db artifact (or is unknown). */
function isRunUsable(run: RunInfo): boolean {
  const avail = run.artifact_availability;
  if (!avail) return true;
  return (
    avail.alerts === true ||
    avail.correlation_evidence === true ||
    avail.events === true ||
    avail.database === true
  );
}

/** A single selectable row in the data source browser. */
type SourceEntry =
  | { type: 'sample' }
  | { type: 'demo' }
  | { type: 'file'; snapshot: LocalImport }
  | { type: 'import' }
  | { type: 'run'; run: RunInfo };

type SourceGroup = {
  key: 'local' | 'files' | 'runs';
  title: string;
  entries: SourceEntry[];
};

const LOCAL_SOURCES: SourceEntry[] = [{ type: 'sample' }, { type: 'demo' }];

function entryLabel(entry: SourceEntry): string {
  switch (entry.type) {
    case 'sample':
      return '样例运行';
    case 'demo':
      return '演示数据';
    case 'file':
      return entry.snapshot.label;
    case 'import':
      return '导入本地数据（JSON / DB）';
    case 'run':
      return runLabel(entry.run);
  }
}

function entryMeta(entry: SourceEntry): string | null {
  switch (entry.type) {
    case 'sample':
      return '内置';
    case 'demo':
      return '演示';
    case 'file':
      // Db imports carry real correlation evidence + full-table counts; JSON
      // snapshots only expose graph + alerts.
      return isDbImport(entry.snapshot)
        ? `${entry.snapshot.nodeCount} 节点 · ${entry.snapshot.alertCount} 告警 · ${entry.snapshot.evidenceCount} 证据 · ${entry.snapshot.claimCount} 声明 · ${entry.snapshot.tracebackCount} 回溯 · ${entry.snapshot.intentCount} 意图 · ${entry.snapshot.actionCount} 动作`
        : `${entry.snapshot.nodeCount} 节点 · ${entry.snapshot.alertCount} 告警`;
    case 'import':
      return null;
    case 'run':
      return entry.run.scenario_ids?.length ? entry.run.run_id.slice(0, 8) : null;
  }
}

function isSelected(entry: SourceEntry, source: DataSourceMeta | null): boolean {
  switch (entry.type) {
    case 'sample':
      return source?.kind === 'sample';
    case 'demo':
      return source?.kind === 'demo';
    case 'file':
      return (
        (source?.kind === 'file' && source.label === entry.snapshot.label) ||
        (source?.kind === 'db' && source.label === entry.snapshot.label)
      );
    case 'run':
      return (
        (source?.kind === 'run' && source.runId === entry.run.run_id) ||
        (source?.kind === 'db' && source.runId === entry.run.run_id)
      );
    case 'import':
      return false;
  }
}

function EntryIcon({ entry }: { entry: SourceEntry }) {
  switch (entry.type) {
    case 'sample':
      return <Sparkles size={13} style={{ opacity: 0.7, flexShrink: 0 }} />;
    case 'demo':
      return <FlaskConical size={13} style={{ opacity: 0.7, flexShrink: 0 }} />;
    case 'file':
      return <FileJson size={13} style={{ opacity: 0.7, flexShrink: 0 }} />;
    case 'import':
      return <Upload size={13} style={{ opacity: 0.7, flexShrink: 0 }} />;
    case 'run':
      return <Database size={13} style={{ opacity: 0.7, flexShrink: 0 }} />;
  }
}

export default function RunSelector() {
  const dataSource = useAppStore((s) => s.dataSource);

  const [runs, setRuns] = useState<RunInfo[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [importedFiles, setImportedFiles] = useState<LocalImport[]>([]);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const dragDepth = useRef(0);

  // Backend runs — optional; local sources work regardless of its availability.
  useEffect(() => {
    let cancelled = false;
    api
      .getRuns()
      .then((data: any) => {
        if (cancelled) return;
        const list = data?.runs ?? data ?? [];
        setRuns(list.filter((r: RunInfo) => isRunUsable(r)));
        setRunsError(null);
      })
      .catch(() => {
        if (!cancelled) setRunsError('后端不可用');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Apply a parsed import to the store and remember it for re-selection. */
  const applyImport = useCallback((local: LocalImport) => {
    setImportedFiles((prev) => prev.filter((s) => s.id !== local.id).concat(local));
    if (isDbImport(local)) {
      useAppStore.getState().loadDatabase(local);
    } else {
      useAppStore.getState().loadSnapshot(local);
    }
  }, []);

  const importFile = useCallback(
    async (file: File) => {
      setImportError(null);
      setLoadError(null);
      try {
        if (/\.(db|sqlite|sqlite3)$/i.test(file.name)) {
          const bytes = new Uint8Array(await file.arrayBuffer());
          applyImport(await loadDatabaseFromBytes(bytes, file.name));
        } else {
          const text = await file.text();
          applyImport(parseSnapshotJson(text, file.name));
        }
        setOpen(false);
      } catch (err) {
        setImportError(err instanceof Error ? err.message : '导入失败，请检查文件内容');
      }
    },
    [applyImport],
  );

  const selectEntry = useCallback(
    async (entry: SourceEntry) => {
      if (entry.type === 'import') {
        fileInputRef.current?.click();
        return;
      }
      setLoadError(null);
      setImportError(null);
      setLoading(true);
      try {
        switch (entry.type) {
          case 'sample':
            await useAppStore.getState().loadSample();
            break;
          case 'demo':
            useAppStore.getState().loadDemo();
            break;
          case 'file':
            applyImport(entry.snapshot);
            break;
          case 'run':
            await useAppStore.getState().loadRun(entry.run.run_id);
            break;
        }
        setOpen(false);
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : '加载失败，请重试');
      } finally {
        setLoading(false);
      }
    },
    [applyImport],
  );

  const handleFilePick = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) void importFile(file);
      e.target.value = '';
    },
    [importFile],
  );

  // ── Search / grouping ──
  const groups = useMemo<SourceGroup[]>(() => {
    const q = query.trim().toLowerCase();
    const matches = (entry: SourceEntry) => {
      if (!q) return true;
      return (
        entryLabel(entry).toLowerCase().includes(q) ||
        (entry.type === 'run' && entry.run.run_id.toLowerCase().includes(q))
      );
    };

    const groups: SourceGroup[] = [];

    const local = LOCAL_SOURCES.filter(matches);
    if (local.length > 0) groups.push({ key: 'local', title: '本地数据源', entries: local });

    const files: SourceEntry[] = [];
    importedFiles.forEach((snapshot) => {
      const entry: SourceEntry = { type: 'file', snapshot };
      if (matches(entry)) files.push(entry);
    });
    // The import row stays pinned unless actively searching.
    if (!q) files.push({ type: 'import' });
    if (files.length > 0) groups.push({ key: 'files', title: '本地文件', entries: files });

    const runEntries = runs.filter((r) => {
      const entry: SourceEntry = { type: 'run', run: r };
      return matches(entry);
    }).map((run) => ({ type: 'run', run }) as SourceEntry);
    if (runEntries.length > 0) groups.push({ key: 'runs', title: '后端会话', entries: runEntries });

    return groups;
  }, [runs, importedFiles, query]);

  // Flat list mirrors exactly what is rendered → keyboard nav can't skip rows.
  const flatEntries = useMemo(() => groups.flatMap((g) => g.entries), [groups]);

  useEffect(() => {
    setActiveIndex(0);
  }, [open, query]);

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${activeIndex}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, flatEntries.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && flatEntries[activeIndex]) {
        e.preventDefault();
        void selectEntry(flatEntries[activeIndex]);
      } else if (e.key === 'Escape') {
        setOpen(false);
      }
    },
    [activeIndex, flatEntries, selectEntry],
  );

  // ── Trigger button label / icon ──
  const sourceLabel = useMemo(() => {
    if (!dataSource) return '导入数据';
    if (dataSource.kind === 'run') {
      const run = runs.find((r) => r.run_id === dataSource.runId);
      return run ? runLabel(run) : dataSource.runId.slice(0, 12);
    }
    if (dataSource.kind === 'db' && dataSource.runId) {
      // A backend run consumed from its db — show the scenario label.
      const run = runs.find((r) => r.run_id === dataSource.runId);
      if (run) return runLabel(run);
    }
    return dataSource.label;
  }, [dataSource, runs]);

  const ButtonIcon = !dataSource
    ? Upload
    : dataSource.kind === 'run' || dataSource.kind === 'db'
      ? Database
      : dataSource.kind === 'sample'
        ? Sparkles
        : dataSource.kind === 'demo'
          ? FlaskConical
          : FileJson;

  // ── Drag & drop import ──
  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  };
  const handleDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void importFile(file);
  };

  let flatIdx = 0;

  return (
    <div style={{ position: 'relative' }}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={sourceLabel}
        data-testid="data-source-trigger"
        onClick={() => {
          setOpen((v) => !v);
          setQuery('');
          setLoadError(null);
          setImportError(null);
        }}
        style={{
          display: 'flex', alignItems: 'center', gap: 7,
          padding: '4px 10px', borderRadius: 6,
          border: '1px solid rgba(255,255,255,0.08)',
          background: open ? 'rgba(255,255,255,0.06)' : 'transparent',
          color: loadError ? 'rgba(248,113,113,0.85)' : 'rgba(255,255,255,0.75)',
          fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap',
          transition: 'background 120ms, border-color 120ms',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.background = 'transparent'; }}
        title={loadError ?? undefined}
      >
        {loading ? (
          <Loader2 size={15} className="mtl-spinner" style={{ opacity: 0.7 }} />
        ) : (
          <ButtonIcon size={15} style={{ opacity: 0.75 }} />
        )}
        <span style={{ fontWeight: 500 }}>{sourceLabel}</span>
        <ChevronDown
          size={13}
          style={{ opacity: 0.4, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 150ms' }}
        />
      </button>

      <input
        ref={fileInputRef}
        type="file"
        accept=".json,application/json,.db,.sqlite,.sqlite3,application/x-sqlite3"
        onChange={handleFilePick}
        style={{ display: 'none' }}
        aria-hidden
        tabIndex={-1}
      />

      {open && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 99 }} onClick={() => setOpen(false)} />
          <div
            role="listbox"
            aria-label="数据源"
            onKeyDown={handleKeyDown}
            onDragEnter={handleDragEnter}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            style={{
              position: 'absolute', top: 'calc(100% + 6px)', right: 0,
              width: 440, maxHeight: 480,
              background: 'rgba(13,14,15,0.98)',
              backdropFilter: 'blur(20px)',
              border: dragging ? '1px dashed rgba(94,106,210,0.6)' : '1px solid rgba(255,255,255,0.08)',
              borderRadius: 12,
              overflow: 'hidden', zIndex: 100,
              boxShadow: '0 16px 48px rgba(0,0,0,0.4)',
              display: 'flex', flexDirection: 'column',
              transition: 'border-color 100ms',
            }}
          >
            {/* Header + search */}
            <div style={{ padding: '12px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
              <div
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '0 10px', height: 30, borderRadius: 6,
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}
              >
                <Search size={13} style={{ opacity: 0.4, flexShrink: 0 }} />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索运行、场景或文件…"
                  autoFocus
                  aria-label="搜索数据源"
                  style={{
                    flex: 1, background: 'none', border: 'none', outline: 'none',
                    fontSize: 12, color: 'rgba(255,255,255,0.85)', fontFamily: 'inherit',
                  }}
                />
                <kbd style={{ fontSize: 9, opacity: 0.35, fontFamily: 'var(--ln-mono, monospace)' }}>esc</kbd>
              </div>
            </div>

            {/* Inline errors */}
            {(loadError || importError) && (
              <div
                role="alert"
                style={{
                  margin: '8px 12px 0', padding: '6px 10px', borderRadius: 6,
                  background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.18)',
                  color: 'rgba(248,113,113,0.85)', fontSize: 11,
                }}
              >
                {importError ?? loadError}
              </div>
            )}

            {/* Drag-drop hint */}
            {dragging && (
              <div
                style={{
                  margin: '8px 12px 0', padding: '10px 12px', borderRadius: 8,
                  background: 'rgba(94,106,210,0.12)', border: '1px solid rgba(94,106,210,0.35)',
                  color: 'rgba(196,201,255,0.9)', fontSize: 11, fontWeight: 500,
                  textAlign: 'center',
                }}
              >
                松开以导入数据文件（JSON / DB）
              </div>
            )}

            {/* List */}
            <div ref={listRef} style={{ maxHeight: 400, overflowY: 'auto', padding: 6 }}>
              {groups.map((group) => (
                <div key={group.key}>
                  <div
                    style={{
                      padding: '8px 12px 4px', fontSize: 10, fontWeight: 600,
                      color: 'rgba(255,255,255,0.3)', letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                    }}
                  >
                    {group.title} · {group.entries.length}
                  </div>
                  {group.entries.map((entry) => {
                    const idx = flatIdx++;
                    const selected = isSelected(entry, dataSource);
                    const highlighted = idx === activeIndex;
                    const meta = entryMeta(entry);
                    return (
                      <button
                        key={
                          entry.type === 'run'
                            ? `run:${entry.run.run_id}`
                            : entry.type === 'file'
                              ? `file:${entry.snapshot.id}`
                              : entry.type
                        }
                        type="button"
                        role="option"
                        aria-selected={selected}
                        data-idx={idx}
                        onClick={() => void selectEntry(entry)}
                        onMouseEnter={() => setActiveIndex(idx)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          width: '100%', textAlign: 'left',
                          padding: '7px 12px', border: 'none', borderRadius: 6, cursor: 'pointer',
                          background: selected
                            ? 'rgba(94,106,210,0.12)'
                            : highlighted
                              ? 'rgba(255,255,255,0.05)'
                              : 'transparent',
                          color: selected ? '#fafafa' : 'rgba(255,255,255,0.6)',
                          fontSize: 11, fontFamily: 'var(--ln-mono, monospace)',
                          transition: 'background 80ms',
                        }}
                      >
                        <EntryIcon entry={entry} />
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {entryLabel(entry)}
                        </span>
                        {meta && (
                          <span
                            style={{
                              flexShrink: 0, fontSize: 9, color: 'rgba(255,255,255,0.3)',
                              fontFamily: 'var(--ln-mono, monospace)',
                            }}
                          >
                            {meta}
                          </span>
                        )}
                        {selected && <Check size={13} style={{ color: '#5e6ad2', flexShrink: 0 }} />}
                      </button>
                    );
                  })}
                </div>
              ))}

              {runsError && runs.length === 0 && (
                <div style={{ padding: '6px 12px', fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>
                  {runsError}，已为你展示本地数据源
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
