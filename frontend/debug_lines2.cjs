const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rules = await page.evaluate(() => {
    const title = document.querySelector('.incident-rail__panel-title');
    
    return {
      before: window.getComputedStyle(title, '::before').content !== 'none' ? window.getComputedStyle(title, '::before').borderBottom : 'none',
      after: window.getComputedStyle(title, '::after').content !== 'none' ? window.getComputedStyle(title, '::after').borderBottom : 'none',
      afterHeight: window.getComputedStyle(title, '::after').height,
      afterBg: window.getComputedStyle(title, '::after').background,
    };
  });
  
  console.log(JSON.stringify(rules, null, 2));
  await browser.close();
})();
