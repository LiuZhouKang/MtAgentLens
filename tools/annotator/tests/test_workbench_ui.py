# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Browser behavior for the offline Cytoscape/ECharts workbench, when Playwright exists."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_workbench_visualization import (
    add_collector_source,
    fixture_inputs,
)
from tools.annotator.workbench import create_server

ROOT = Path(__file__).resolve().parents[3]
PLAYWRIGHT = ROOT / "frontend" / "node_modules" / "playwright"
BROWSERS = Path.home() / ".cache" / "ms-playwright"

NODE = r'''
const { chromium } = require('playwright');

function launchErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push('pageerror: ' + error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push('console: ' + message.text());
  });
  return errors;
}

async function waitForSelection(page, eventId) {
  await page.waitForFunction(
    (expected) => document.querySelector('#selection-id')?.textContent === expected,
    eventId, { timeout: 15000 });
}

async function waitForEvidence(page) {
  await page.waitForFunction(
    () => !document.querySelector('#download-raw').hidden &&
      document.querySelector('#evidence-content').textContent.length > 0,
    null, { timeout: 15000 });
}

(async () => {
  const browser = await chromium.launch({ headless: true, chromiumSandbox: false });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = launchErrors(page);
  await page.goto(process.argv[2], { waitUntil: 'networkidle' });
  await page.waitForSelector('.task');
  await page.click('.task');
  await page.waitForFunction(
    () => (document.querySelector('#relation-canvas')?._cy?.nodes().length || 0) > 0,
    null, { timeout: 20000 });
  await page.waitForTimeout(700);

  const defaultKind = await page.locator('#relation-kind').inputValue();
  if (defaultKind !== 'delegation_record') throw new Error('Expected a readable Agent delegation view by default');
  await page.selectOption('#relation-kind', '');
  await page.waitForTimeout(300);

  const initial = await page.evaluate(() => {
    const el = document.querySelector('#relation-canvas');
    const cy = el._cy;
    const queue = document.querySelector('#queue-panel').getBoundingClientRect();
    const inspect = document.querySelector('#inspection-panel').getBoundingClientRect();
    const editor = document.querySelector('#editor-panel').getBoundingClientRect();
    return {
      nodes: cy.nodes().length,
      edges: cy.edges().length,
      canvases: el.querySelectorAll('canvas').length,
      stats: [...document.querySelectorAll('.summary-stat b')].map((node) => node.textContent),
      picker: document.querySelector('#relation-evidence-picker').options.length,
      relationStatus: document.querySelector('#relation-status').textContent,
      selectionVisible: !document.querySelector('#selection-detail').hidden,
      selectionSource: document.querySelector('#selection-source').textContent,
      selectionTime: document.querySelector('#selection-time').textContent,
      selectionId: document.querySelector('#selection-id').textContent,
      downloadVisible: !document.querySelector('#download-raw').hidden,
      rawVisible: document.querySelector('#evidence-content').textContent.length > 0,
      queueWidth: Math.round(queue.width),
      inspectWidth: Math.round(inspect.width),
      editorWidth: Math.round(editor.width),
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth,
      injectedNodes: cy.nodes().filter((node) =>
        String(node.data('labelFull')).includes('<img')).length,
      injectedImgs: document.querySelectorAll('img').length,
      xss: Boolean(window.__xss),
    };
  });

  const nodePoint = await page.evaluate(() => {
    const el = document.querySelector('#relation-canvas');
    const cy = el._cy;
    const position = cy.getElementById('agent:agent-a').renderedPosition();
    const rect = el.getBoundingClientRect();
    return { x: rect.x + position.x, y: rect.y + position.y };
  });
  await page.mouse.click(nodePoint.x, nodePoint.y);
  await waitForSelection(page, 'delegation_records:d1');
  await waitForEvidence(page);
  const nodeClick = await page.evaluate(() => {
    const cy = document.querySelector('#relation-canvas')._cy;
    return {
      selected: cy.$(':selected').length,
      faded: cy.elements('.faded').length,
      selectionId: document.querySelector('#selection-id').textContent,
      source: document.querySelector('#selection-source').textContent,
      time: document.querySelector('#selection-time').textContent,
      content: document.querySelector('#evidence-content').textContent,
    };
  });

  const edgePoint = await page.evaluate(() => {
    const el = document.querySelector('#relation-canvas');
    const cy = el._cy;
    const edge = cy.edges().filter((item) => item.data('kind') === 'process_parenthood')[0];
    const position = edge.renderedMidpoint();
    const rect = el.getBoundingClientRect();
    return { id: edge.id(), eventId: edge.data('eventId'),
      x: rect.x + position.x, y: rect.y + position.y };
  });
  await page.mouse.click(edgePoint.x, edgePoint.y);
  await waitForSelection(page, edgePoint.eventId);
  await waitForEvidence(page);
  const edgeClick = await page.evaluate((expected) => {
    const cy = document.querySelector('#relation-canvas')._cy;
    return {
      selectionId: document.querySelector('#selection-id').textContent,
      expected,
      selected: cy.$(':selected').length,
      faded: cy.elements('.faded').length,
      content: document.querySelector('#evidence-content').textContent,
    };
  }, edgePoint.eventId);

  await page.fill('#relation-search', 'agent-a');
  await page.waitForTimeout(250);
  const search = await page.evaluate(() => {
    const cy = document.querySelector('#relation-canvas')._cy;
    return { nodes: cy.nodes().length, edges: cy.edges().length,
      status: document.querySelector('#relation-status').textContent };
  });
  await page.fill('#relation-search', '');
  await page.waitForTimeout(250);
  await page.selectOption('#relation-kind', 'delegation_record');
  await page.waitForTimeout(250);
  const typeFilter = await page.evaluate(() => {
    const cy = document.querySelector('#relation-canvas')._cy;
    return { edges: cy.edges().length,
      kinds: cy.edges().map((edge) => edge.data('kind')) };
  });
  await page.selectOption('#relation-kind', '');
  await page.waitForTimeout(200);

  const zoomBefore = await page.evaluate(() =>
    document.querySelector('#relation-canvas')._cy.zoom());
  await page.click('#relation-zoom-in');
  await page.waitForTimeout(150);
  const zoomAfter = await page.evaluate(() =>
    document.querySelector('#relation-canvas')._cy.zoom());
  await page.click('#relation-reset');
  await page.waitForTimeout(350);

  await page.click('#trace-view-timeline');
  await page.waitForFunction(
    () => (document.querySelector('#timeline-canvas')?._chart
      ?.getOption()?.series?.[0]?.data?.length || 0) > 0,
    null, { timeout: 15000 });
  await page.waitForTimeout(500);
  const timeline = await page.evaluate(() => {
    const el = document.querySelector('#timeline-canvas');
    const chart = el._chart;
    const option = chart.getOption();
    const data = option.series[0].data;
    const item = data.find((entry) => entry.value[0] > 0.1) || data[0];
    const pixel = chart.convertToPixel({ seriesIndex: 0 }, item.value);
    const rect = el.getBoundingClientRect();
    return {
      series: data.length,
      domains: document.querySelectorAll('#timeline-domain option').length,
      canvas: el.querySelectorAll('canvas').length,
      dataZoom: option.dataZoom.length,
      tooltipMode: option.tooltip?.[0]?.renderMode,
      status: document.querySelector('#timeline-status').textContent,
      point: { eventId: item.eventId, x: rect.x + pixel[0], y: rect.y + pixel[1] },
    };
  });
  await page.mouse.click(timeline.point.x, timeline.point.y);
  await waitForSelection(page, timeline.point.eventId);
  await waitForEvidence(page);
  const timelineClick = await page.evaluate(() => {
    const option = document.querySelector('#timeline-canvas')._chart.getOption();
    const selected = option.series[0].data.find((item) => item.symbolSize > 10);
    return {
      selectionId: document.querySelector('#selection-id').textContent,
      selected: selected ? selected.eventId : null,
      symbolSize: selected ? selected.symbolSize : null,
      status: document.querySelector('#timeline-status').textContent,
    };
  });

  await page.fill('#timeline-search', 'process_events');
  await page.waitForTimeout(350);
  const timelineFilter = await page.evaluate(() => ({
    series: document.querySelector('#timeline-canvas')._chart.getOption().series[0].data.length,
    status: document.querySelector('#timeline-status').textContent,
  }));
  await page.fill('#timeline-search', '');
  await page.waitForTimeout(250);
  await page.evaluate(() => {
    document.querySelector('#timeline-canvas')._chart.dispatchAction(
      { type: 'dataZoom', start: 0, end: 40 });
  });
  await page.waitForTimeout(350);
  const timelineZoom = await page.evaluate(() => ({
    status: document.querySelector('#timeline-status').textContent,
    zoom: document.querySelector('#timeline-canvas')._chart.getOption().dataZoom
      .map((item) => [item.start, item.end]),
  }));
  await page.click('#timeline-reset');
  await page.waitForTimeout(300);

  await page.click('#trace-view-events');
  await page.waitForSelector('#event-list .event-button');
  const eventId = await page.locator('#event-list .event-button').first()
    .getAttribute('data-event-id');
  await page.locator('#event-list .event-button').first().click();
  await waitForSelection(page, eventId);
  await waitForEvidence(page);
  const events = await page.evaluate(() => ({
    count: document.querySelectorAll('#event-list .event-button').length,
    selectionId: document.querySelector('#selection-id').textContent,
    content: document.querySelector('#evidence-content').textContent.length,
    downloadVisible: !document.querySelector('#download-raw').hidden,
  }));

  await page.click('#trace-view-graph');
  await page.waitForTimeout(400);
  const injectedPoint = await page.evaluate(() => {
    const el = document.querySelector('#relation-canvas');
    const cy = el._cy;
    const node = cy.nodes().filter((item) =>
      String(item.data('labelFull')).includes('<img'))[0];
    const position = node.renderedPosition();
    const rect = el.getBoundingClientRect();
    return { x: rect.x + position.x, y: rect.y + position.y };
  });
  await page.mouse.move(injectedPoint.x, injectedPoint.y);
  await page.waitForTimeout(350);
  const tooltip = await page.evaluate(() => {
    const tip = document.querySelector('#graph-tooltip');
    return {
      hidden: tip.hidden,
      text: tip.textContent,
      imgs: tip.querySelectorAll('img').length,
    };
  });
  await page.mouse.move(5, 5);
  await page.waitForTimeout(250);

  const responsive = [];
  for (const [width, height] of [[1440, 1000], [1024, 900], [768, 900]]) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(600);
    responsive.push(await page.evaluate(() => {
      const box = (selector) => {
        const rect = document.querySelector(selector).getBoundingClientRect();
        return { x: Math.round(rect.x), y: Math.round(rect.y),
          w: Math.round(rect.width), h: Math.round(rect.height),
          right: Math.round(rect.right), bottom: Math.round(rect.bottom) };
      };
      const overlap = (one, two) =>
        !(one.right <= two.x || two.right <= one.x ||
          one.bottom <= two.y || two.bottom <= one.y);
      const queue = box('#queue-panel');
      const inspect = box('#inspection-panel');
      const editor = box('#editor-panel');
      return {
        width: innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
        queue, inspect, editor,
        graph: box('#relation-canvas'),
        overlapQueueInspect: overlap(queue, inspect),
        overlapInspectEditor: overlap(inspect, editor),
        editorOverflow: document.querySelector('#editor-panel').scrollHeight >
          document.querySelector('#editor-panel').clientHeight,
      };
    }));
  }

  console.log(JSON.stringify({
    errors, initial, nodeClick, edgeClick, search, typeFilter,
    zoom: { before: zoomBefore, after: zoomAfter },
    timeline, timelineClick, timelineFilter, timelineZoom, events, tooltip, responsive,
  }));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
'''


NODE_EMPTY = r'''
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, chromiumSandbox: false });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push('pageerror: ' + error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push('console: ' + message.text());
  });
  await page.goto(process.argv[2], { waitUntil: 'networkidle' });
  await page.waitForSelector('.task');
  await page.click('.task');
  await page.waitForFunction(() => {
    const status = document.querySelector('#relation-status')?.textContent || '';
    return status.startsWith('显示 0/0');
  }, null, { timeout: 15000 });
  const graph = await page.evaluate(() => {
    const el = document.querySelector('#relation-canvas');
    return {
      emptyVisible: !document.querySelector('#relation-empty').hidden,
      emptyText: document.querySelector('#relation-empty').textContent,
      cyNodes: el._cy ? el._cy.nodes().length : 0,
      picker: document.querySelector('#relation-evidence-picker').options.length,
    };
  });
  await page.click('#trace-view-timeline');
  await page.waitForTimeout(700);
  const timeline = await page.evaluate(() => {
    const el = document.querySelector('#timeline-canvas');
    const chart = el._chart;
    return {
      emptyVisible: !document.querySelector('#timeline-empty').hidden,
      emptyText: document.querySelector('#timeline-empty').textContent,
      series: chart ? (chart.getOption()?.series?.[0]?.data?.length || 0) : 0,
      domains: document.querySelectorAll('#timeline-domain option').length,
    };
  });
  console.log(JSON.stringify({ errors, graph, timeline }));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
'''


def _chromium_available():
    return any(path.is_dir() for path in BROWSERS.glob("chromium-*"))


def _run_node(script_text, url, tmp_path):
    script = tmp_path / "ui-check.cjs"
    script.write_text(script_text)
    environment = dict(os.environ, NODE_PATH=str(ROOT / "frontend" / "node_modules"))
    result = subprocess.run(["node", str(script), url], timeout=120,
                            capture_output=True, text=True, env=environment)
    if result.returncode != 0:
        raise AssertionError("browser script failed:\n" + result.stderr + result.stdout)
    return json.loads(result.stdout)


def _serve(store_path):
    server = create_server(store_path, rater="ui-rater", method="controlled_execution",
                           mode="annotate", token="ui-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.mark.skipif(shutil.which("node") is None or not PLAYWRIGHT.is_dir() or
                    not _chromium_available(),
                    reason="Node Playwright and a Chromium build are unavailable")
def test_chart_views_are_interactive_and_open_exact_evidence(tmp_path):
    root = tmp_path / "ui"
    root.mkdir()
    spec = fixture_inputs(root)
    rows = [
        {"kind": "row", "table": "process_events", "primary_id": "p1",
         "row": {"pid": 100, "start_time_ns": 4000, "comm": "bash",
                 "timestamp_ns": 8000}},
        {"kind": "row", "table": "process_events", "primary_id": "p2",
         "row": {"pid": 200, "start_time_ns": 5000, "parent_pid": 100,
                 "parent_start_time_ns": 4000, "comm": "node", "timestamp_ns": 9000}},
        {"kind": "row", "table": "delegation_records", "primary_id": "d1",
         "row": {"from_agent_id": "agent-a", "to_agent_id": "agent-b",
                 "timestamp_ns": 9500, "confidence": "high"}},
        {"kind": "row", "table": "subagent_boundaries", "primary_id": "b1",
         "row": {"parent_agent_id": "agent-b", "child_agent_id": "agent-c",
                 "timestamp_ns": 9600, "reason": "spawn"}},
        {"kind": "row", "table": "delegation_records", "primary_id": "d2",
         "row": {"from_agent_id": "<img src=x onerror=window.__xss=1>",
                 "to_agent_id": "agent-c", "timestamp_ns": 9550}},
        {"kind": "row", "table": "actions", "primary_id": "a1",
         "row": {"action_type": "FileOpen", "pid": 200, "timestamp_ns": 9700}},
    ]
    spec = add_collector_source(root, spec, rows)
    build_bundle(root / "bundle", **spec)
    store_path = root / "records.sqlite"
    RecordStore(store_path).register_bundle(root / "bundle")
    server, thread = _serve(store_path)
    try:
        url = "http://127.0.0.1:%d/#token=ui-token" % server.server_port
        report = _run_node(NODE, url, tmp_path)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert report["errors"] == []
    initial = report["initial"]
    assert initial["canvases"] >= 1
    assert initial["nodes"] >= 5 and initial["edges"] >= 3
    assert len(initial["stats"]) == 6
    assert initial["picker"] >= 6
    assert initial["relationStatus"].startswith("显示 ")
    assert initial["selectionVisible"] is True
    assert initial["selectionSource"] != "—" and initial["selectionTime"] != "—"
    assert initial["selectionId"]
    assert initial["downloadVisible"] is True and initial["rawVisible"] is True
    assert 180 <= initial["queueWidth"] <= 220
    assert 340 <= initial["editorWidth"] <= 360
    assert initial["inspectWidth"] > initial["editorWidth"]
    assert initial["scrollWidth"] <= initial["innerWidth"]
    assert initial["injectedNodes"] == 1
    assert initial["injectedImgs"] == 0 and initial["xss"] is False

    assert report["nodeClick"]["selectionId"] == "delegation_records:d1"
    assert report["nodeClick"]["selected"] == 1
    assert report["nodeClick"]["faded"] > 0
    assert '"table": "delegation_records"' in report["nodeClick"]["content"]
    assert report["edgeClick"]["selectionId"] == report["edgeClick"]["expected"]
    assert report["edgeClick"]["expected"] == "process_events:p2"
    assert report["edgeClick"]["selected"] == 1
    assert '"table": "process_events"' in report["edgeClick"]["content"]

    assert 0 < report["search"]["nodes"] < initial["nodes"]
    assert report["search"]["edges"] >= 1
    assert report["typeFilter"]["edges"] == 2
    assert set(report["typeFilter"]["kinds"]) == {"delegation_record"}
    assert report["zoom"]["after"] > report["zoom"]["before"]

    timeline = report["timeline"]
    assert timeline["series"] >= 4
    assert timeline["domains"] == 1
    assert timeline["canvas"] == 1
    assert timeline["dataZoom"] == 2
    assert timeline["tooltipMode"] == "richText"
    assert "点" in timeline["status"]
    assert report["timelineClick"]["selected"] == timeline["point"]["eventId"]
    assert report["timelineClick"]["symbolSize"] > 10
    assert "已选点" in report["timelineClick"]["status"]
    assert report["timelineFilter"]["series"] < timeline["series"]
    assert "窗口" in report["timelineZoom"]["status"]
    assert report["timelineZoom"]["zoom"][0][0] > 0 or report["timelineZoom"]["zoom"][0][1] < 100

    assert report["events"]["count"] >= 5
    assert report["events"]["selectionId"]
    assert report["events"]["content"] > 0
    assert report["events"]["downloadVisible"] is True
    assert report["tooltip"]["hidden"] is False
    assert "<img" in report["tooltip"]["text"]
    assert report["tooltip"]["imgs"] == 0
    assert report["initial"]["xss"] is False

    for viewport in report["responsive"]:
        assert viewport["scrollWidth"] <= viewport["width"]
        assert viewport["overlapQueueInspect"] is False
        assert viewport["overlapInspectEditor"] is False
        assert viewport["graph"]["w"] > 0 and viewport["graph"]["h"] >= 200
    desktop = report["responsive"][0]
    assert 340 <= desktop["editor"]["w"] <= 360
    narrow = report["responsive"][1]
    assert narrow["inspect"]["w"] > narrow["editor"]["w"]
    assert narrow["editor"]["w"] <= 340


@pytest.mark.skipif(shutil.which("node") is None or not PLAYWRIGHT.is_dir() or
                    not _chromium_available(),
                    reason="Node Playwright and a Chromium build are unavailable")
def test_missing_relations_and_timestamps_do_not_draw_fake_data(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    spec = fixture_inputs(root)
    build_bundle(root / "bundle", **spec)
    store_path = root / "records.sqlite"
    RecordStore(store_path).register_bundle(root / "bundle")
    server, thread = _serve(store_path)
    try:
        url = "http://127.0.0.1:%d/#token=ui-token" % server.server_port
        report = _run_node(NODE_EMPTY, url, tmp_path)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

    assert report["errors"] == []
    assert report["graph"]["emptyVisible"] is True
    assert "没有结构化关系" in report["graph"]["emptyText"]
    assert report["graph"]["cyNodes"] == 0
    assert report["graph"]["picker"] == 1
    assert report["timeline"]["emptyVisible"] is True
    assert report["timeline"]["series"] == 0
    assert report["timeline"]["domains"] == 0
