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
  
  // Scroll the rail body down
  await page.evaluate(() => {
    const rail = document.querySelector('.incident-rail__panel-content');
    if (rail) rail.scrollTop = rail.scrollHeight;
  });
  
  await page.waitForTimeout(500);
  await page.screenshot({ path: 'incident_rail_v4_bottom.png', fullPage: false });
  
  console.log('Screenshot saved to incident_rail_v4_bottom.png');
  await browser.close();
})();
