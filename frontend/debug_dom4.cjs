const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rects = await page.evaluate(() => {
    const flow = document.querySelector('.incident-panel-flow');
    return {
      display: window.getComputedStyle(flow).display,
      flexDirection: window.getComputedStyle(flow).flexDirection,
      height: window.getComputedStyle(flow).height,
      gridTemplateRows: window.getComputedStyle(flow).gridTemplateRows,
    };
  });
  
  console.log(JSON.stringify(rects, null, 2));
  await browser.close();
})();
