const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rules = await page.evaluate(() => {
    const hero = document.querySelector('.incident-rail__panel-hero');
    const title = document.querySelector('.incident-rail__panel-title');
    const heading = document.querySelector('.incident-rail__panel-heading');
    const kicker = document.querySelector('.incident-rail__panel-kicker');
    
    return {
      hero: {
        borderTop: window.getComputedStyle(hero).borderTop,
        borderBottom: window.getComputedStyle(hero).borderBottom,
        boxShadow: window.getComputedStyle(hero).boxShadow,
      },
      title: {
        borderTop: window.getComputedStyle(title).borderTop,
        borderBottom: window.getComputedStyle(title).borderBottom,
        boxShadow: window.getComputedStyle(title).boxShadow,
      },
      heading: {
        borderTop: window.getComputedStyle(heading).borderTop,
        borderBottom: window.getComputedStyle(heading).borderBottom,
        boxShadow: window.getComputedStyle(heading).boxShadow,
      },
      kicker: {
        borderTop: window.getComputedStyle(kicker).borderTop,
        borderBottom: window.getComputedStyle(kicker).borderBottom,
        boxShadow: window.getComputedStyle(kicker).boxShadow,
      }
    };
  });
  
  console.log(JSON.stringify(rules, null, 2));
  await browser.close();
})();
