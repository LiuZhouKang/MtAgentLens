const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rules = await page.evaluate(() => {
    const kicker = document.querySelector('.incident-rail__panel-kicker');
    const hero = document.querySelector('.incident-rail__panel-hero');
    const header = document.querySelector('.incident-rail__panel-header');
    return {
      kickerLine: window.getComputedStyle(kicker).borderBottom,
      kickerBg: window.getComputedStyle(kicker).background,
      heroLine: window.getComputedStyle(hero).borderBottom,
      headerLine: window.getComputedStyle(header).borderBottom,
      kickerHeight: window.getComputedStyle(kicker).height,
      kickerDisplay: window.getComputedStyle(kicker).display,
    };
  });
  
  console.log(JSON.stringify(rules, null, 2));
  await browser.close();
})();
