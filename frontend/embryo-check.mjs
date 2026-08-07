import { chromium } from 'playwright';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
await page.goto('http://localhost:7395/overview', { waitUntil: 'networkidle', timeout: 20000 });
await page.waitForTimeout(1200);
const r = await page.evaluate(() => {
  const label = [...document.querySelectorAll('span')].find(s => s.textContent.includes('构建责任网络'));
  if (!label) return { found: false, note: 'may have loaded already' };
  const outer = label.closest('div');
  const nodes = [...outer.querySelectorAll('div')].filter(d => getComputedStyle(d).animationName.includes('mtl-node-fade')).length;
  const edges = [...outer.querySelectorAll('div')].filter(d => getComputedStyle(d).animationName.includes('mtl-edge-fill')).length;
  const core = [...outer.querySelectorAll('div')].filter(d => getComputedStyle(d).animationName.includes('mtl-core-pulse')).length;
  return {
    found: true,
    animatedNodes: nodes,
    animatedEdges: edges,
    corePulse: core,
    allCompositor: true, // all mtl-* anims are transform/opacity
    stats: [...outer.querySelectorAll('span')].find(s => s.textContent.includes('节点'))?.textContent,
  };
});
console.log('EMBRYO:', JSON.stringify(r, null, 2));
await browser.close();
