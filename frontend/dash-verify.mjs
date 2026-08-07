import { chromium } from 'playwright';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
await page.goto('http://localhost:7395/overview', { waitUntil: 'networkidle', timeout: 20000 });
await page.waitForTimeout(1500);
const r = await page.evaluate(() => {
  // Check KPI cards exist (light components, should render immediately)
  const kpi = document.querySelectorAll('.ln-metric').length;
  // Check Sigma network panel head exists
  const panelHead = [...document.querySelectorAll('h3')].filter(h => h.textContent.includes('实时责任网络')).length;
  // Check if NetworkLoading is currently visible inside the graph area
  const loadingText = [...document.querySelectorAll('span')].find(s => s.textContent.includes('责任网络构建'));
  const graphLoaded = [...document.querySelectorAll('canvas')].length > 0;
  return { kpiCards: kpi, panelHeadExists: panelHead > 0, loadingShown: !!loadingText, graphLoaded, errors: errors.length };
});
console.log('DASH-VERIFY:', JSON.stringify(r, null, 2));
await browser.close();
