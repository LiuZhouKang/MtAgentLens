const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
  });
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rects = await page.evaluate(() => {
    const summary = document.querySelector('.incident-panel-summary');
    const flow = document.querySelector('.incident-panel-flow');
    const statusline = document.querySelector('.incident-brief__statusline');
    const copy = document.querySelector('.incident-brief__copy');
    const factList = document.querySelector('.incident-fact-list');
    
    return {
      flow: flow ? flow.getBoundingClientRect() : null,
      summary: summary ? summary.getBoundingClientRect() : null,
      statusline: statusline ? statusline.getBoundingClientRect() : null,
      copy: copy ? copy.getBoundingClientRect() : null,
      factList: factList ? factList.getBoundingClientRect() : null,
      summaryStyle: summary ? {
        display: window.getComputedStyle(summary).display,
        visibility: window.getComputedStyle(summary).visibility,
        opacity: window.getComputedStyle(summary).opacity,
        height: window.getComputedStyle(summary).height,
        position: window.getComputedStyle(summary).position,
      } : null
    };
  });
  
  console.log(JSON.stringify(rects, null, 2));
  await browser.close();
})();
