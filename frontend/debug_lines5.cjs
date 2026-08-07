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
    const kicker = document.querySelector('.incident-rail__panel-kicker');
    
    return {
      heroBg: window.getComputedStyle(hero).backgroundImage,
      titleBg: window.getComputedStyle(title).backgroundImage,
      kickerBg: window.getComputedStyle(kicker).backgroundImage,
    };
  });
  
  console.log(JSON.stringify(rules, null, 2));
  await browser.close();
})();
