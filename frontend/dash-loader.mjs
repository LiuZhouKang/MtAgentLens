import { chromium } from 'playwright';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
// Navigate to overview page and wait for SigmaNetwork loading state
await page.goto('http://localhost:7395/overview', { waitUntil: 'networkidle', timeout: 20000 });
await page.waitForTimeout(1500);
const r = await page.evaluate(() => {
  // Find the SigmaNetwork loading overlay (contains "正在布局责任网络…")
  const spans = [...document.querySelectorAll('span')];
  const loadingSpan = spans.find(s => s.textContent.includes('正在布局责任网络'));
  if (!loadingSpan) return { found: false, note: 'may already have loaded' };
  const overlay = loadingSpan.parentElement?.parentElement;
  const rings = overlay?.querySelectorAll('div[style*="mtl-ring-rotate"]');
  const stats = overlay?.querySelector('span[style*="font-size: 9px"]');
  return {
    found: true,
    overlayExists: !!overlay,
    spinningRings: rings?.length || 0,
    nodeStats: stats?.textContent?.trim(),
    hasMtlCorePulse: !!overlay?.querySelector('div[style*="mtl-core-pulse"]'),
  };
});
console.log('DASH-LOADER:', JSON.stringify(r, null, 2));
await browser.close();
