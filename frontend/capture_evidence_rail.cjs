const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
  });
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  
  // Wait a bit for animations
  await page.waitForTimeout(2000);
  
  // Click on the incident-rail dock item to expand it
  await page.click('.incident-rail__navitem');
  
  // Wait for the panel to expand
  await page.waitForTimeout(1000);
  
  await page.screenshot({ path: 'incident_rail_v4_expanded.png', fullPage: false });
  
  console.log('Screenshot saved to incident_rail_v4_expanded.png');
  await browser.close();
})();
