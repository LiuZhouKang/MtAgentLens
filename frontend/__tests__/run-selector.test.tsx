// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import initSqlJs from 'sql.js';

// Isolate the store: initData falls through to demo fixtures with no backend.
vi.mock('@/lib/api', () => ({
  api: {
    getRuns: vi.fn().mockResolvedValue([]),
    getGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
    getAlerts: vi.fn().mockResolvedValue([]),
    getEvidence: vi
      .fn()
      .mockResolvedValue({ evidence: [], intents: [], actions: [], non_links: [] }),
    getRunDb: vi.fn().mockRejectedValue(new Error('no db endpoint')),
  },
}));
vi.mock('@/lib/load-sample-data', () => ({
  loadSampleData: vi.fn().mockResolvedValue(null),
}));

import RunSelector from '@/components/system/run-selector';
import { useAppStore } from '@/lib/store';
import { api } from '@/lib/api';
import { loadSampleData } from '@/lib/load-sample-data';

const sampleData = {
  graph: {
    nodes: [
      {
        node_id: 's1',
        node_type: 'Agent',
        agent_id: 'a',
        resource: null,
        evidence_refs: [],
        timestamp_ns: 0,
        phase: 'Provisional',
      },
    ],
    edges: [],
  },
  alerts: [],
  metrics: {
    node_counts: { Agent: 1 },
    edge_counts: {},
    agent_count: 1,
    cross_agent_edges: 0,
    delegation_depth: 0,
    taint_chains: 0,
    avg_confidence: 0,
  },
};

const validSnapshotJson = JSON.stringify({
  graph: {
    nodes: [
      {
        node_id: 'n1',
        node_type: 'Agent',
        agent_id: 'a',
        resource: null,
        evidence_refs: [],
        timestamp_ns: 0,
        phase: 'Provisional',
      },
    ],
    edges: [],
  },
  alerts: [],
});

function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

function openSelector() {
  render(<RunSelector />);
  fireEvent.click(screen.getByTestId('data-source-trigger'));
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    dataSource: null,
    activeRunId: null,
    graph: { nodes: [], edges: [] },
    alerts: [],
    errorMessage: null,
  });
  vi.mocked(api.getRuns).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
});

describe('RunSelector — data source browser', () => {
  it('is never empty: shows local sources even when the backend is unavailable', async () => {
    vi.mocked(api.getRuns).mockRejectedValue(new Error('offline'));
    openSelector();

    expect((await screen.findByText(/本地数据源/)).textContent).toContain('本地数据源');
    expect(screen.getByText('样例运行')).toBeTruthy();
    expect(screen.getByText('演示数据')).toBeTruthy();
    expect(screen.getByText('导入本地数据（JSON / DB）')).toBeTruthy();
    expect((await screen.findByText(/后端不可用/)).textContent).toMatch(/后端不可用/);
  });

  it('loads the built-in sample when selected', async () => {
    vi.mocked(loadSampleData).mockResolvedValueOnce(sampleData as never);
    openSelector();

    fireEvent.click(await screen.findByText('样例运行'));

    await waitFor(() =>
      expect(useAppStore.getState().dataSource).toEqual({ kind: 'sample', label: '样例运行' }),
    );
  });

  it('loads the demo fixtures when selected', async () => {
    openSelector();

    fireEvent.click(screen.getByText('演示数据'));

    await waitFor(() =>
      expect(useAppStore.getState().dataSource).toEqual({ kind: 'demo', label: '演示数据' }),
    );
  });

  it('lists backend runs and loads the chosen one', async () => {
    vi.mocked(api.getRuns).mockResolvedValueOnce([
      {
        run_id: 'r-123',
        scenario_ids: ['travel-planning'],
        run_status: 'completed',
        artifact_availability: { alerts: true, correlation_evidence: true },
      },
    ] as never);
    openSelector();

    fireEvent.click(await screen.findByText('travel-planning'));

    await waitFor(() =>
      expect(useAppStore.getState().dataSource).toEqual({ kind: 'run', runId: 'r-123' }),
    );
    expect(vi.mocked(api.getGraph)).toHaveBeenCalledWith('r-123');
  });

  it('imports a SQLite db file and applies graph/alerts/evidence', async () => {
    const buf = readFileSync(join(process.cwd(), '__tests__', 'fixtures', 'db-sample.sqlite'));
    openSelector();

    fireEvent.change(fileInput(), {
      target: { files: [new File([buf], 'run-42.db', { type: 'application/x-sqlite3' })] },
    });

    await waitFor(() =>
      expect(useAppStore.getState().dataSource).toEqual({ kind: 'db', label: 'run-42.db' }),
    );
    expect(useAppStore.getState().graph.nodes).toHaveLength(3);
    expect(useAppStore.getState().alerts).toHaveLength(2);
    expect(useAppStore.getState().evidence).toHaveLength(3);
  });

  it('shows real db counts on a re-selected db import', async () => {
    const buf = readFileSync(join(process.cwd(), '__tests__', 'fixtures', 'db-sample.sqlite'));
    openSelector();
    fireEvent.change(fileInput(), {
      target: { files: [new File([buf], 'run-42.db', { type: 'application/x-sqlite3' })] },
    });
    await waitFor(() =>
      expect(useAppStore.getState().dataSource).toEqual({ kind: 'db', label: 'run-42.db' }),
    );

    fireEvent.click(screen.getByTestId('data-source-trigger'));
    expect(
      (await screen.findByText(/3 节点 · 2 告警 · 3 证据 · 2 声明 · 1 回溯 · 3 意图 · 3 动作/))
        .textContent,
    ).toMatch(/3 节点 · 2 告警 · 3 证据 · 2 声明 · 1 回溯 · 3 意图 · 3 动作/);
  });

  it('imports a valid JSON snapshot from the file picker', async () => {
    openSelector();

    fireEvent.change(fileInput(), {
      target: {
        files: [new File([validSnapshotJson], 'run-42.json', { type: 'application/json' })],
      },
    });

    await waitFor(() =>
      expect(useAppStore.getState().dataSource).toEqual({ kind: 'file', label: 'run-42.json' }),
    );
  });

  it('keeps an imported snapshot as a re-selectable local file', async () => {
    openSelector();
    fireEvent.change(fileInput(), {
      target: {
        files: [new File([validSnapshotJson], 'run-42.json', { type: 'application/json' })],
      },
    });
    await waitFor(() =>
      expect(useAppStore.getState().dataSource).toEqual({ kind: 'file', label: 'run-42.json' }),
    );

    // Popover closed after import; reopen from the trigger, which now shows the file label.
    fireEvent.click(screen.getByTestId('data-source-trigger'));
    expect((await screen.findByText(/1 节点 · 0 告警/)).textContent).toMatch(/1 节点 · 0 告警/);
  });

  it('shows an inline error for an invalid snapshot and keeps the popover open', async () => {
    openSelector();

    fireEvent.change(fileInput(), {
      target: { files: [new File(['{oops'], 'bad.json', { type: 'application/json' })] },
    });

    expect((await screen.findByRole('alert')).textContent).toMatch(/不是有效的 JSON/);
    expect(useAppStore.getState().dataSource).toBeNull();
  });

  it('filters entries by query and hides the import row while searching', async () => {
    openSelector();

    const input = screen.getByLabelText('搜索数据源');
    fireEvent.change(input, { target: { value: '演示' } });

    expect(screen.getByText('演示数据')).toBeTruthy();
    expect(screen.queryByText('样例运行')).toBeNull();
    expect(screen.queryByText('导入本地数据（JSON / DB）')).toBeNull();
  });

  it('imports an empty / unrelated sqlite db → reports "no responsibility data" and keeps current source', async () => {
    // 用 sql.js 现造一个「有效 sqlite 但无责任表」的空库（正是用户拖入无关 db 的场景）
    const SQL = await initSqlJs();
    const db = new SQL.Database();
    db.run('CREATE TABLE unrelated (x INTEGER)');
    db.run('INSERT INTO unrelated VALUES (1)');
    // sql.js export 返回 Uint8Array<ArrayBufferLike>，拷贝成 ArrayBuffer 才能作 BlobPart
    const buf = new Uint8Array(db.export());
    db.close();

    openSelector();
    fireEvent.change(fileInput(), {
      target: { files: [new File([buf], 'notes.db', { type: 'application/x-sqlite3' })] },
    });

    expect((await screen.findByRole('alert')).textContent).toMatch(/不含责任数据/);
    // 不污染当前数据源 + 错误写入 store（错误横幅跨页可读）
    expect(useAppStore.getState().dataSource).toBeNull();
    expect(useAppStore.getState().errorMessage).toMatch(/不含责任数据/);
  });

  it('imports a non-sqlite *.db → surfaces the parse error in the store too', async () => {
    openSelector();
    fireEvent.change(fileInput(), {
      target: {
        files: [
          new File(['not a database at all'], 'broken.db', { type: 'application/x-sqlite3' }),
        ],
      },
    });

    expect((await screen.findByRole('alert')).textContent).toMatch(/不是有效的 SQLite/);
    expect(useAppStore.getState().errorMessage).toMatch(/不是有效的 SQLite/);
    expect(useAppStore.getState().dataSource).toBeNull();
  });
});
