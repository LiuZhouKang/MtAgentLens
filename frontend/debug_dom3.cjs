const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rects = await page.evaluate(() => {
    const summary = document.querySelector('.incident-panel-summary');
    return {
      overflow: window.getComputedStyle(summary).overflow,
      flex: window.getComputedStyle(summary).flex,
      height: window.getComputedStyle(summary).height,
      minHeight: window.getComputedStyle(summary).minHeight,
      maxHeight: window.getComputedStyle(summary).maxHeight,
    };
  });
  
  console.log(JSON.stringify(rects, null, 2));
  await browser.close();
})();
