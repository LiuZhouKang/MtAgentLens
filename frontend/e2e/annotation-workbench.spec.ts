import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const root = path.resolve(__dirname, '../..');

// Synthetic controlled-execution records exercise the assigned roster; these are not human annotations.
const bootstrap = String.raw`
import hashlib,json,sys,threading
from pathlib import Path
from tools.annotator.tests.test_evidence import fixture_inputs
from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.workbench import create_server
root=Path(sys.argv[1]); spec=fixture_inputs(root)
raw=spec['sources']['runner']['path']
raw.write_text(json.dumps({'native_id':'r1','text':'<img src=x onerror="window.injected=1">'},ensure_ascii=False)+'\n')
spec['events'][0].update(byte_length=raw.stat().st_size,record_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
build_bundle(root/'bundle',**spec)
store=root/'records.sqlite'; RecordStore(store).register_bundle(root/'bundle')
urls={}
raters={'alice':'刘周康','bob':'唐浩钺','third':'刘锦样','curator':'curator'}
for alias,rater in raters.items():
    server=create_server(store,rater=rater,method='controlled_execution',
        mode='adjudicate' if rater=='curator' else 'annotate',
        reviewers=tuple(name for key,name in raters.items() if key!='curator') if rater=='curator' else (),
        token='test-'+alias,port=0,codebook_version='pilot/1')
    threading.Thread(target=server.serve_forever,daemon=True).start()
    urls[alias]='http://127.0.0.1:'+str(server.server_port)+'/#token=test-'+alias
print(json.dumps(urls),flush=True)
threading.Event().wait()
`;

// One frozen bundle, a synthetic model draft, and both an independent-annotate
// server and a studio (annotate + review) server on the same store.
const studioBootstrap = String.raw`
import hashlib,json,sys,threading
from pathlib import Path
from tools.annotator.tests.test_evidence import fixture_inputs
from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.workbench import create_server
root=Path(sys.argv[1]); spec=fixture_inputs(root)
raw=spec['sources']['runner']['path']
raw.write_text(json.dumps({'native_id':'r1','text':'<img src=x onerror="window.injected=1">'},ensure_ascii=False)+'\n')
spec['events'][0].update(byte_length=raw.stat().st_size,record_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
build_bundle(root/'bundle',**spec)
records=RecordStore(root/'records.sqlite')
task=records.register_bundle(root/'bundle')
draft={'attempted':{'value':'yes','evidence_refs':['event-1']},
 'executed':{'value':'unknown','evidence_refs':[]},
 'succeeded':{'value':'unknown','evidence_refs':[]},
 'identities':{'event-1':None},'roots':{'actors':None,'evidence_refs':[]},
 'notes':'DRAFT-SECRET-MARKER 模型草稿仅供复核'}
records.save(task,'model-proposal:agent',draft,method='model_proposal',expected_revision=None)
urls={}
def serve(alias,rater,mode,token):
    server=create_server(root/'records.sqlite',rater=rater,method='controlled_execution',mode=mode,
        token=token,port=0,codebook_version='pilot/1')
    threading.Thread(target=server.serve_forever,daemon=True).start()
    urls[alias]='http://127.0.0.1:'+str(server.server_port)+'/#token='+token
serve('annotate','solo-rater','annotate','test-solo')
serve('studio','studio-rater','studio','test-studio')
print(json.dumps(urls),flush=True)
threading.Event().wait()
`;

// A 5000-event frozen bundle: the scale contract for chunked rendering and
// pane switching on one page.
const heavyBootstrap = String.raw`
import hashlib,json,sys,threading
from pathlib import Path
from tools.annotator.tests.test_evidence import fixture_inputs
from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.workbench import create_server
root=Path(sys.argv[1]); spec=fixture_inputs(root)
raw=spec['sources']['runner']['path']
events=[]; lines=[]; offset=0; count=5000
for index in range(count):
    line=json.dumps({'native_id':'r%d'%index,'pid':42,'kind':'assistant_message','text':'step %d'%index})+'\n'
    payload=line.encode()
    events.append({'event_id':'event-%d'%index,'source_id':'runner','record_id':'r%d'%index,
        'timestamp':str(index),'clock_domain':'runner-monotonic-ns','derivation':'native record id',
        'byte_offset':offset,'byte_length':len(payload),
        'record_sha256':hashlib.sha256(payload).hexdigest()})
    offset+=len(payload); lines.append(line)
raw.write_text(''.join(lines))
spec['events']=events
build_bundle(root/'bundle',**spec)
records=RecordStore(root/'records.sqlite')
task=records.register_bundle(root/'bundle')
draft={'attempted':{'value':'unknown','evidence_refs':[]},
 'executed':{'value':'unknown','evidence_refs':[]},
 'succeeded':{'value':'unknown','evidence_refs':[]},
 'identities':{'event-%d'%i:None for i in range(count)},
 'roots':{'actors':None,'evidence_refs':[]},'notes':'DRAFT-SECRET-MARKER'}
records.save(task,'model-proposal:agent',draft,method='model_proposal',expected_revision=None)
server=create_server(root/'records.sqlite',rater='studio-rater',method='controlled_execution',mode='studio',
    token='test-heavy',port=0,codebook_version='pilot/1')
threading.Thread(target=server.serve_forever,daemon=True).start()
urls={'studio':'http://127.0.0.1:'+str(server.server_port)+'/#token=test-heavy'}
print(json.dumps(urls),flush=True)
threading.Event().wait()
`;

async function startWorkbench(script: string = bootstrap, startupTimeout = 20_000) {
  const directory = mkdtempSync(path.join(tmpdir(), 'mtal-workbench-'));
  const child = spawn(path.join(root, 'env/.venv/bin/python'), ['-u', '-c', script, directory], {
    cwd: root,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let errors = '';
  child.stderr.on('data', (chunk) => (errors += chunk.toString()));
  try {
    const urls = await new Promise<Record<string, string>>((resolve, reject) => {
      let output = '';
      const timer = setTimeout(
        () => reject(new Error(`Startup timed out: ${errors}`)),
        startupTimeout,
      );
      child.stdout.on('data', (chunk) => {
        output += chunk.toString();
        if (output.includes('\n')) {
          clearTimeout(timer);
          try {
            resolve(JSON.parse(output.split('\n')[0]));
          } catch (error) {
            reject(error);
          }
        }
      });
      child.once('exit', () => {
        clearTimeout(timer);
        reject(new Error(`Workbench exited: ${errors}`));
      });
      child.once('error', reject);
    });
    return {
      urls,
      directory,
      async stop() {
        if (child.exitCode === null && child.signalCode === null) {
          const stopped = new Promise<void>((resolve) => child.once('exit', () => resolve()));
          child.kill();
          await stopped;
        }
        rmSync(directory, { recursive: true, force: true });
      },
    };
  } catch (error) {
    child.kill();
    rmSync(directory, { recursive: true, force: true });
    throw error;
  }
}

interface RecordedRequest {
  url: string;
  method: string;
}

function recordRequests(page: Page): RecordedRequest[] {
  const requests: RecordedRequest[] = [];
  page.on('request', (request) => {
    requests.push({ url: request.url(), method: request.method() });
  });
  return requests;
}

async function expectNoA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      nodes: violation.nodes.map((node) => node.target),
    })),
  ).toEqual([]);
}

test('independent raters inspect evidence, retain stale drafts and adjudicate exact revisions', async ({
  browser,
}, testInfo) => {
  const app = await startWorkbench();
  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  try {
    const [alice, bob, third, curator] = await Promise.all(
      contexts.map((context) => context.newPage()),
    );
    await alice.goto(app.urls.alice);
    await expect(alice.getByRole('heading', { name: '证据标注工作台' })).toBeVisible();
    await alice.locator('[data-task-id]').first().click();
    await alice.locator('[data-event-id]').first().click();
    await expect(alice.locator('#evidence-content')).toContainText('<img');
    await expect(alice.locator('#evidence-content img')).toHaveCount(0);
    await alice.getByLabel('身份归属 event-1').selectOption('actor-a');
    await alice.locator('[name=notes]').fill('刘周康的独立测试记录');
    await alice.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(alice.locator('#status')).toContainText('已保存');

    const stale = await contexts[0].newPage();
    await stale.goto(app.urls.alice);
    await stale.locator('[data-task-id]').first().click();
    await expect(stale.locator('[name=notes]')).toHaveValue('刘周康的独立测试记录');
    await alice.locator('[name=notes]').fill('刘周康更新后的独立测试记录');
    await alice.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(alice.locator('#status')).toContainText('已保存');
    await stale.locator('[name=notes]').fill('冲突后仍须保留的草稿');
    await stale.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(stale.locator('#status')).toContainText('冲突');
    await expect(stale.locator('[name=notes]')).toHaveValue('冲突后仍须保留的草稿');

    await bob.goto(app.urls.bob);
    await bob.locator('[data-task-id]').first().click();
    await expect(bob.locator('[name=notes]')).toHaveValue('');
    await expect(bob.locator('body')).not.toContainText('刘周康更新后的独立测试记录');
    await bob.getByLabel('身份归属 event-1').selectOption('actor-b');
    await bob.locator('[name=notes]').fill('唐浩钺的独立测试记录');
    await bob.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(bob.locator('#status')).toContainText('已保存');

    await third.goto(app.urls.third);
    await third.locator('[data-task-id]').first().click();
    await expect(third.locator('[name=notes]')).toHaveValue('');
    await expect(third.locator('body')).not.toContainText('刘周康更新后的独立测试记录');
    await expect(third.locator('body')).not.toContainText('唐浩钺的独立测试记录');
    await third.getByLabel('身份归属 event-1').selectOption('actor-a');
    await third.locator('[name=notes]').fill('刘锦样的独立测试记录');
    await third.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(third.locator('#status')).toContainText('已保存');

    await curator.goto(app.urls.curator);
    await curator.locator('[data-task-id]').first().click();
    await expect(curator.locator('#comparison')).toContainText('刘周康更新后的独立测试记录');
    await expect(curator.locator('#comparison')).toContainText('唐浩钺的独立测试记录');
    await expect(curator.locator('#comparison')).toContainText('刘锦样的独立测试记录');
    await expect(curator.locator('[data-difference-field="identities.event-1"]')).toBeVisible();
    await curator.getByLabel('身份归属 event-1').selectOption('actor-a');
    await curator.locator('[name=rationale]').fill('核对原始记录后选择 actor-a，保留三份原标注。');
    await curator.getByRole('button', { name: '保存裁决', exact: true }).click();
    await expect(curator.locator('#status')).toContainText('已保存');
    const downloadPromise = curator.waitForEvent('download');
    await curator.getByRole('button', { name: '导出完整队列', exact: true }).click();
    const download = await downloadPromise;
    const file = await download.path();
    expect(file).not.toBeNull();
    const exported = JSON.parse(readFileSync(file!, 'utf8'));
    expect(exported.paper_metrics_available).toBe(false);
    expect(exported.tasks[0].revisions).toHaveLength(5);
    const decision = exported.tasks[0].revisions.find(
      (record: { annotator: string }) => record.annotator === 'curator',
    );
    expect(Object.keys(decision.review.reviewed_revisions).sort()).toEqual(
      ['刘周康', '唐浩钺', '刘锦样'].sort(),
    );
    expect(decision.review.rationale).toContain('核对原始记录');
    await curator.screenshot({
      path: testInfo.outputPath('annotation-adjudication.png'),
      fullPage: true,
    });
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    await app.stop();
  }
});

test('studio page exposes tab semantics and arrow-key navigation between panes', async ({
  page,
}) => {
  const app = await startWorkbench(studioBootstrap);
  try {
    await page.goto(app.urls.studio);
    const tablist = page.getByRole('tablist', { name: '工作模式' });
    await expect(tablist).toBeVisible();
    const annotateTab = page.getByRole('tab', { name: '独立标注' });
    const reviewTab = page.getByRole('tab', { name: '草稿复核' });
    await expect(annotateTab).toHaveAttribute('aria-selected', 'true');
    await expect(annotateTab).toHaveAttribute('tabindex', '0');
    await expect(reviewTab).toHaveAttribute('aria-selected', 'false');
    await expect(reviewTab).toHaveAttribute('tabindex', '-1');
    await expect(annotateTab).toHaveAttribute('aria-controls', 'annotate-panel');
    await expect(reviewTab).toHaveAttribute('aria-controls', 'review-area');
    await expect(page.locator('#annotate-panel')).toHaveAttribute('role', 'tabpanel');
    await expect(page.locator('#review-area')).toHaveAttribute('role', 'tabpanel');

    await annotateTab.focus();
    await page.keyboard.press('ArrowRight');
    await expect(reviewTab).toBeFocused();
    await expect(reviewTab).toHaveAttribute('aria-selected', 'true');
    await expect(reviewTab).toHaveAttribute('tabindex', '0');
    await expect(annotateTab).toHaveAttribute('tabindex', '-1');
    await expect(page.locator('#review-area')).toBeVisible();

    await page.keyboard.press('ArrowLeft');
    await expect(annotateTab).toBeFocused();
    await expect(annotateTab).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('#annotate-panel')).toBeVisible();

    await page.keyboard.press('End');
    await expect(reviewTab).toBeFocused();
    await page.keyboard.press('Home');
    await expect(annotateTab).toBeFocused();
    await expect(page.locator('#annotate-panel')).toBeVisible();
  } finally {
    await app.stop();
  }
});

test('annotate pane never requests or renders a model draft until review is opened', async ({
  page,
}) => {
  const app = await startWorkbench(studioBootstrap);
  try {
    const requests = recordRequests(page);
    await page.goto(app.urls.studio);
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#evidence-content')).toContainText('native_id');
    await expect(page.locator('#review-area')).toBeHidden();
    await expect(page.locator('#annotate-panel')).toBeVisible();
    await expect(page.locator('#proposal-content')).toHaveText('');
    await expect(page.locator('#proposal-list .proposal-card')).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText('DRAFT-SECRET-MARKER');
    const leaked = requests.filter(
      (request) => request.url.includes('review=1') || request.url.includes('/attestations'),
    );
    expect(leaked).toEqual([]);

    const reviewTab = page.getByRole('tab', { name: '草稿复核' });
    await reviewTab.click();
    await expect(page.locator('#review-area')).toBeVisible();
    await expect(page.locator('#proposal-content')).toContainText('DRAFT-SECRET-MARKER');
    const reviewRequests = requests.filter((request) => request.url.includes('review=1'));
    expect(reviewRequests.length).toBeGreaterThanOrEqual(2);
    // The review queue and task payload are fetched only after the explicit switch.
    expect(reviewRequests.some((request) => request.url.includes('/api/tasks?'))).toBe(true);
    expect(
      reviewRequests.some((request) =>
        /\/api\/tasks\/task-\d+\?review=1$/.test(request.url),
      ),
    ).toBe(true);

    const before = requests.length;
    await page.getByRole('tab', { name: '独立标注' }).click();
    await expect(page.locator('#annotate-panel')).toBeVisible();
    await expect(page.locator('#review-area')).toBeHidden();
    // Leaving the pane clears the draft DOM so annotating never displays it.
    await expect(page.locator('#proposal-content')).toHaveText('');
    const reread = requests.slice(before);
    expect(reread.some((request) => /\/api\/tasks\/task-\d+$/.test(request.url))).toBe(true);
    expect(reread.every((request) => !request.url.includes('/attestations'))).toBe(true);
  } finally {
    await app.stop();
  }
});

test('review pane records statements and never writes human annotations', async ({ page }) => {
  const app = await startWorkbench(studioBootstrap);
  try {
    const requests = recordRequests(page);
    await page.goto(app.urls.studio);
    await page.getByRole('tab', { name: '草稿复核' }).click();
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#proposal-content')).toContainText('DRAFT-SECRET-MARKER');
    await expect(page.locator('#annotation-form')).toBeHidden();
    await expect(page.getByRole('button', { name: '保存标注', exact: true })).toBeHidden();

    await page.locator('#attestation-statement').fill('已核对证据与草稿，仅记录复核声明。');
    await page.getByRole('button', { name: '记录复核声明', exact: true }).click();
    await expect(page.locator('#review-status')).toContainText('已记录');
    await expect(page.locator('#attestation-list')).toContainText('仅记录复核声明');
    await expect(page.locator('[data-task-id]').first()).toContainText('已复核');
    const posts = requests.filter((request) => request.method === 'POST');
    expect(posts.every((request) => request.url.includes('/attestations'))).toBe(true);

    await page.getByRole('tab', { name: '独立标注' }).click();
    await expect(page.locator('#annotation-form')).toBeVisible();
    await page.locator('[name=notes]').fill('复核页只写声明；标注页写入人工标注。');
    await page.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(page.locator('#status')).toContainText('已保存');
    await page.getByRole('tab', { name: '草稿复核' }).click();
    await expect(page.locator('#attestation-list')).toContainText('仅记录复核声明');
    expect(requests.filter((request) => request.method === 'POST')
      .some((request) => request.url.includes('/annotations'))).toBe(true);
  } finally {
    await app.stop();
  }
});

test('standalone, studio and review panes have no axe violations', async ({ page }) => {
  const app = await startWorkbench(studioBootstrap);
  try {
    await page.goto(app.urls.studio);
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#evidence-content')).toContainText('native_id');
    await expectNoA11yViolations(page);

    await page.getByRole('tab', { name: '草稿复核' }).click();
    await expect(page.locator('#proposal-content')).toContainText('DRAFT-SECRET-MARKER');
    await expectNoA11yViolations(page);

    // A pure independent-annotate server hides the tablist entirely; the same
    // markup must stay clean without the tab roles being exercised.
    await page.goto(app.urls.annotate);
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#evidence-content')).toContainText('native_id');
    await expect(page.locator('#studio-tabs')).toBeHidden();
    await expectNoA11yViolations(page);
  } finally {
    await app.stop();
  }
});

test('a 412 precondition failure reports a conflict and keeps the draft', async ({ page }) => {
  const app = await startWorkbench();
  try {
    await page.route('**/annotations', (route) =>
      route.fulfill({
        status: 412,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'precondition_failed', message: '记录已被更新。' }),
      }),
    );
    await page.goto(app.urls.alice);
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#evidence-content')).toContainText('native_id');
    await page.locator('[name=notes]').fill('412 冲突后仍然保留的草稿');
    await page.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(page.locator('#status')).toContainText('冲突');
    await expect(page.locator('#status')).toContainText('当前草稿未丢弃');
    await expect(page.locator('#dirty-indicator')).toHaveAttribute('data-dirty', 'true');
    await expect(page.locator('[name=notes]')).toHaveValue('412 冲突后仍然保留的草稿');
    await expect(page.locator('#a11y-live')).toContainText('冲突');
    await expect(page.getByRole('button', { name: '保存标注', exact: true })).toBeEnabled();
  } finally {
    await app.stop();
  }
});

test('unknown and evidenced-empty roots round-trip distinctly and notes stay text', async ({
  page,
  request,
}) => {
  const app = await startWorkbench();
  try {
    await page.goto(app.urls.alice);
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#evidence-content')).toContainText('native_id');
    await expect(page.locator('[name=roots_state]')).toHaveValue('unknown');
    await expect(page.locator('#roots-meaning')).toContainText('null');

    await page.locator('[name=roots_state]').selectOption('none');
    await expect(page.locator('#roots-meaning')).toContainText('空集合');
    await page.locator('[name=root_refs]').first().check();
    const payload = '<img src=x onerror="window.injected=1">备注仍是纯文本';
    await page.locator('[name=notes]').fill(payload);
    await page.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(page.locator('#status')).toContainText('已保存');

    await page.locator('#history > summary').click();
    await expect(page.locator('#history-list .history-record')).toHaveCount(1);
    await expect(page.locator('#history-list')).toContainText('备注仍是纯文本');
    await expect(page.locator('#history-list')).toContainText('<img src=x');
    await expect(page.locator('#history-list img')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { injected?: number }).injected)).toBeUndefined();

    await page.getByRole('button', { name: '重新载入', exact: true }).click();
    await expect(page.locator('[name=roots_state]')).toHaveValue('none');
    await expect(page.locator('[name=notes]')).toHaveValue(payload);

    await page.locator('[name=roots_state]').selectOption('unknown');
    await expect(page.locator('#roots-meaning')).toContainText('null');
    await page.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(page.locator('#status')).toContainText('已保存');

    const response = await request.get(
      new URL('/api/tasks/task-0001', app.urls.alice).href,
      { headers: { Authorization: 'Bearer test-alice' } },
    );
    expect(response.ok()).toBe(true);
    const view = await response.json();
    expect(view.own.annotation.roots.actors).toBeNull();
    expect(view.history[0].annotation.roots.actors).toEqual([]);
    expect(view.history[0].annotation.roots.evidence_refs).toEqual(['event-1']);
  } finally {
    await app.stop();
  }
});

test('five thousand event task opens, switches panes and pages without freezing', async ({
  page,
}) => {
  test.setTimeout(180_000);
  const app = await startWorkbench(heavyBootstrap, 60_000);
  try {
    await page.goto(app.urls.studio);
    const openedAt = Date.now();
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#event-count')).toContainText('120 / 5000', { timeout: 30_000 });
    expect(Date.now() - openedAt).toBeLessThan(15_000);
    await expect(page.locator('[data-event-id]')).toHaveCount(120);

    await page.locator('#event-more').click();
    await expect(page.locator('#event-count')).toContainText('240 / 5000');
    await expect(page.locator('[data-event-id]')).toHaveCount(240);

    const reviewAt = Date.now();
    await page.getByRole('tab', { name: '草稿复核' }).click();
    await expect(page.locator('#proposal-content')).toContainText('DRAFT-SECRET-MARKER', {
      timeout: 30_000,
    });
    expect(Date.now() - reviewAt).toBeLessThan(15_000);

    const annotateAt = Date.now();
    await page.getByRole('tab', { name: '独立标注' }).click();
    await expect(page.locator('#annotate-panel')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-event-id]')).toHaveCount(120, { timeout: 30_000 });
    expect(Date.now() - annotateAt).toBeLessThan(15_000);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
  } finally {
    await app.stop();
  }
});

test('annotation controls, studio tabs and blinded evidence remain usable on a narrow screen', async ({
  browser,
}, testInfo) => {
  const app = await startWorkbench(studioBootstrap);
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  try {
    const page = await context.newPage();
    await page.goto(app.urls.studio);
    await page.locator('[data-task-id]').first().click();
    await page.locator('[data-event-id]').first().click();
    await expect(page.locator('#evidence-content')).toContainText('native_id');
    await expect(page.locator('#meta-method')).toContainText('pilot/1');
    await expect(page.getByRole('button', { name: '下载脱敏记录', exact: true })).toBeVisible();

    // Real tab structure survives the stack; both tabs stay tappable.
    const tabs = page.locator('#studio-tabs [role="tab"]');
    await expect(tabs).toHaveCount(2);
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
    await expect(tabs.nth(1)).toHaveAttribute('tabindex', '-1');
    const tabHeights = await tabs.evaluateAll((nodes) =>
      nodes.map((node) => node.getBoundingClientRect().height),
    );
    expect(Math.min(...tabHeights)).toBeGreaterThanOrEqual(32);

    await tabs.nth(1).click();
    await expect(page.locator('#review-area')).toBeVisible();
    await expect(page.locator('#annotate-panel')).toBeHidden();
    await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
    await expect(tabs.nth(1)).toHaveAttribute('tabindex', '0');
    await expect(tabs.nth(0)).toHaveAttribute('tabindex', '-1');

    await tabs.nth(0).click();
    await expect(page.locator('#annotate-panel')).toBeVisible();
    await expect(page.getByRole('button', { name: '保存标注', exact: true })).toBeVisible();

    // The evidence list is an explicitly scrollable region on a phone.
    const overflowY = await page
      .locator('#event-list')
      .evaluate((node) => getComputedStyle(node).overflowY);
    expect(['auto', 'scroll']).toContain(overflowY);

    const save = page.getByRole('button', { name: '保存标注', exact: true });
    await save.scrollIntoViewIfNeeded();
    const box = await save.boundingBox();
    const viewport = page.viewportSize()!;
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(28);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
    await save.click();
    await expect(page.locator('#status')).toContainText('已保存');

    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
    await page.screenshot({ path: testInfo.outputPath('annotation-mobile.png'), fullPage: true });
  } finally {
    await context.close();
    await app.stop();
  }
});

test('known empty roots save with evidence, history updates and integrity failure retains the draft', async ({
  page,
}) => {
  const app = await startWorkbench();
  try {
    await page.goto(app.urls.alice);
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#evidence-content')).toContainText('native_id');
    await expect(page.locator('#meta-method')).toContainText('pilot/1');
    await expect(page.getByRole('button', { name: '下载脱敏记录', exact: true })).toBeVisible();
    await page.locator('[name=roots_state]').selectOption('none');
    await page.locator('[name=root_refs]').first().check();
    await page.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(page.locator('#status')).toContainText('已保存');
    await page.locator('#history > summary').click();
    await expect(page.locator('#history-list .history-record')).toHaveCount(1);

    const manifest = JSON.parse(
      readFileSync(path.join(app.directory, 'bundle/bundle.json'), 'utf8'),
    );
    writeFileSync(path.join(app.directory, 'bundle', manifest.sources.runner.path), 'tampered');
    await page.locator('[name=notes]').fill('证据失效时必须保留的草稿');
    await page.getByRole('button', { name: '保存标注', exact: true }).click();
    await expect(page.locator('#status')).toContainText('验证失败');
    await expect(page.locator('#evidence-content')).not.toContainText('native_id');
    await expect(page.getByRole('button', { name: '保存标注', exact: true })).toBeDisabled();
    await expect(page.locator('[name=notes]')).toHaveValue('证据失效时必须保留的草稿');
  } finally {
    await app.stop();
  }
});

test('editing the adjudication rationale during a pending save remains an unsaved draft', async ({
  page,
  request,
}) => {
  const app = await startWorkbench();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  try {
    const annotation = {
      attempted: { value: 'unknown', evidence_refs: [] },
      executed: { value: 'unknown', evidence_refs: [] },
      succeeded: { value: 'unknown', evidence_refs: [] },
      identities: { 'event-1': null },
      roots: { actors: null, evidence_refs: [] },
      notes: '',
    };
    for (const rater of ['alice', 'bob', 'third']) {
      const response = await request.post(
        new URL('/api/tasks/task-0001/annotations', app.urls[rater]).href,
        {
          headers: { Authorization: `Bearer test-${rater}` },
          data: { expected_revision: null, annotation },
        },
      );
      expect(response.ok()).toBe(true);
    }
    await page.goto(app.urls.curator);
    await page.locator('[data-task-id]').first().click();
    await expect(page.locator('#comparison')).toContainText('刘周康');
    await page.locator('[name=rationale]').fill('提交时的裁决理由');
    await page.route('**/annotations', async (route) => {
      await gate;
      await route.continue();
    });
    const sent = page.waitForRequest((req) => req.method() === 'POST');
    await page.getByRole('button', { name: '保存裁决', exact: true }).click();
    await sent;
    await page.locator('[name=rationale]').fill('等待保存期间继续修改的理由');
    release();
    await expect(page.locator('#status')).toContainText('已保存');
    await expect(page.locator('#dirty-indicator')).toHaveAttribute('data-dirty', 'true');
    await expect(page.locator('[name=rationale]')).toHaveValue('等待保存期间继续修改的理由');
  } finally {
    release();
    await app.stop();
  }
});
