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
    const children = Array.from(summary.children).map(child => {
      const style = window.getComputedStyle(child);
      return {
        className: child.className,
        position: style.position,
        float: style.float,
        display: style.display,
        height: style.height,
      };
    });
    return children;
  });
  
  console.log(JSON.stringify(rects, null, 2));
  await browser.close();
})();
