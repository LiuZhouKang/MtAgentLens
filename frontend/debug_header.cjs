const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rules = await page.evaluate(() => {
    const el = document.querySelector('.incident-rail__panel-header');
    return el ? window.getComputedStyle(el).borderBottom : 'Not found';
  });
  
  console.log('header border-bottom:', rules);
  await browser.close();
})();
