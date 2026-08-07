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
  
  await page.screenshot({ path: 'incident_rail_v4_top.png', fullPage: false, clip: { x: 0, y: 0, width: 800, height: 400 } });
  
  console.log('Screenshot saved to incident_rail_v4_top.png');
  await browser.close();
})();
