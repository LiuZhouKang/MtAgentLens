import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * 实时模式 e2e —— 通过注入 fake EventSource + fetch mock 模拟后端实时流：
 * 1. 活跃 run → LiveSession 连接 SSE → LiveStatusBar「实时」+ 事件流滚动 + 告警流。
 * 2. run 结束 → 自动转回放信号（LiveStatusBar 回 idle）。
 * 3. 实时连接态 axe a11y（idle 态已在 a11y-axe.spec 覆盖，此补充 connected 态）。
 */

// 与 a11y-axe.spec 一致：color-contrast 属既定深色设计语言，其余规则全开
const DISABLED_RULES = ['color-contrast'];

/** 注入 fake 实时后端：EventSource 自动发射事件 + /api/runs/active 受控返回。 */
async function injectLiveBackend(page: Page) {
  await page.addInitScript(() => {
    (window as any).__activeRun = true;

    const runSummary = () => ({
      run_id: 'live-run-1',
      started_at_ns: Date.now() * 1e6,
    });

    // 受控 fetch：/api/runs/active 按 __activeRun 返回；其余 run 端点给空数组兜底。
    const origFetch = window.fetch.bind(window);
    window.fetch = (url: RequestInfo | URL, opts?: RequestInit) => {
      const u = String(url);
      if (u.includes('/api/runs/active')) {
        const active = (window as any).__activeRun;
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ data: active ? runSummary() : null }),
        } as Response);
      }
      if (u.includes('/api/runs/')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ data: [] }),
        } as Response);
      }
      return origFetch(url, opts);
    };

    // fake EventSource：连接后 onopen，随后发射 2 条原始事件 + 1 条告警。
    class FakeES {
      listeners: Record<string, (e: { data: string; lastEventId?: string }) => void> = {};
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      closed = false;
      constructor(public url: string) {
        setTimeout(() => {
          if (this.closed || !this.onopen) return;
          this.onopen();
          const emit = (type: string, data: unknown, id: string) => {
            if (this.closed) return;
            const cb = this.listeners[type];
            if (cb) cb({ data: JSON.stringify(data), lastEventId: id });
          };
          const ev = (src: string, pid: number, comm: string, data: unknown, ns: number) => ({
            timestamp: Date.now(),
            timestamp_ns: ns,
            source: src,
            pid,
            comm,
            data,
          });
          setTimeout(() => emit('event', ev('tls_capture', 8120, 'claude', { len: 1200 }, 1), '1'), 80);
          setTimeout(() => emit('event', ev('process', 8121, 'builder', { op: 'exec' }, 2), '2'), 180);
          setTimeout(
            () =>
              emit(
                'alert',
                {
                  alert_id: 'a1',
                  timestamp_ns: 3,
                  severity: 'Critical',
                  title: '污染源判定',
                  summary: '检测到污染源',
                  confidence: 0.9,
                  decision: 'Unsafe',
                  authority: 'Authoritative',
                  display_profile: 'UnsafeByEngine',
                  reasoning: 'test',
                  action_hint: null,
                  primary_agent: { agent_id: 'ag1', claimed_role: null },
                  involved_agents: [],
                  engine: {
                    rule_verdicts: [],
                    pattern_verdicts: [],
                    classification_score: 0,
                    classification_route: '',
                    score_breakdown: {
                      rule_score: 0,
                      pattern_score: 0,
                      boundary_score: 0,
                      evidence_score: 0,
                      role_score: 0,
                      repetition_score: 0,
                      behavioral_score: 0,
                      total: 0,
                    },
                  },
                  timeline: { steps: [], total_duration_ms: 0 },
                  graph_snapshot: {
                    snapshot_id: '',
                    timestamp_ns: 3,
                    nodes: [],
                    edges: [],
                    metrics: {
                      node_counts: {},
                      edge_counts: {},
                      agent_count: 0,
                      cross_agent_edges: 0,
                      delegation_depth: 0,
                      taint_chains: 0,
                      avg_confidence: 0,
                    },
                  },
                  phase: { Provisional: { timestamp_ns: 3 } },
                  provisional_ref: null,
                },
                '3',
              ),
            280,
          );
        }, 30);
      }
      addEventListener(type: string, cb: (e: { data: string; lastEventId?: string }) => void) {
        this.listeners[type] = cb;
      }
      close() {
        this.closed = true;
      }
    }
    (window as any).EventSource = FakeES;
  });
}

test.describe('实时模式 /console', () => {
  test('活跃 run：状态栏「实时」+ 原始事件流滚动 + 告警实时插入', async ({ page }) => {
    await injectLiveBackend(page);
    await page.goto('/console', { waitUntil: 'domcontentloaded' });

    // 状态栏进入「实时」
    await expect(page.getByTestId('live-status-bar')).toContainText('实时', { timeout: 8000 });

    // 原始事件流面板直播自动打开，事件行出现（source + comm）
    const tail = page.getByTestId('live-event-tail');
    await expect(tail).toBeVisible({ timeout: 8000 });
    await expect(tail).toContainText('tls_capture', { timeout: 8000 });
    await expect(tail).toContainText('claude', { timeout: 8000 });
    await expect(tail).toContainText('pid 8121', { timeout: 8000 });

    // 实时告警流出现新告警
    const feed = page.getByTestId('live-alert-feed');
    await expect(feed).toContainText('污染源判定', { timeout: 8000 });
    await expect(page.getByTestId('live-badge')).toBeVisible();
  });

  test('run 结束：自动转回放（状态栏回 idle，事件流关闭）', async ({ page }) => {
    await injectLiveBackend(page);
    await page.goto('/console', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('live-status-bar')).toContainText('实时', { timeout: 8000 });

    // 模拟 run 结束：置 __activeRun=false，等下一轮轮询（2s）检测
    await page.evaluate(() => {
      (window as any).__activeRun = false;
    });
    await expect(page.getByTestId('live-status-bar')).toContainText('实时未启用', {
      timeout: 8000,
    });
  });

  test('实时连接态 axe a11y：状态栏/告警流/事件流无违规', async ({ page }) => {
    await injectLiveBackend(page);
    await page.goto('/console', { waitUntil: 'domcontentloaded' });
    await page.locator('#app-main').waitFor();
    // 等到实时 UI 全部就绪：状态栏「实时」+ 事件流出现 + 告警插入
    await expect(page.getByTestId('live-status-bar')).toContainText('实时', { timeout: 8000 });
    await expect(page.getByTestId('live-event-tail')).toContainText('tls_capture', { timeout: 8000 });
    await expect(page.getByTestId('live-alert-feed')).toContainText('污染源判定', { timeout: 8000 });

    const results = await new AxeBuilder({ page })
      .include('#app-main')
      .disableRules(DISABLED_RULES)
      .analyze();
    expect(results.violations).toEqual([]);
  });
});
