const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const summaryBox = await page.$('.incident-panel-summary');
  if (summaryBox) {
    const box = await summaryBox.boundingBox();
    console.log('incident-panel-summary bounding box:', box);
    
    const html = await page.evaluate(el => el.outerHTML, summaryBox);
    console.log('HTML:', html);
  } else {
    console.log('incident-panel-summary NOT FOUND!');
  }
  
  await browser.close();
})();
