const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch({ headless: 'new' });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1080, deviceScaleFactor: 2 });
  
  try {
    await page.goto('http://localhost:7395', { waitUntil: 'load', timeout: 30000 });
    
    // Give it a moment to render animations
    await new Promise(r => setTimeout(r, 2000));
    
    const panels = ['incident', 'evidence', 'trace', 'agent', 'handoff'];
    
    for (const panel of panels) {
      console.log(`Capturing ${panel} panel...`);
      
      // Click the tab in the dock
      const buttons = await page.$$('button.incident-rail__navitem');
      for (const btn of buttons) {
        const ariaControls = await page.evaluate(el => el.getAttribute('aria-controls'), btn);
        const ariaLabel = await page.evaluate(el => el.getAttribute('aria-label'), btn);
        if (ariaLabel && ariaLabel.toLowerCase().includes(panel === 'incident' ? '事件' : panel === 'evidence' ? '证据' : panel === 'trace' ? '链路' : panel === 'agent' ? '主体' : '委派')) {
          await btn.click();
          break;
        }
      }
      
      // Wait for animation
      await new Promise(r => setTimeout(r, 1000));
      
      await page.screenshot({ path: `screenshot_${panel}_panel.png`, fullPage: false });
    }
    
  } catch (e) {
    console.error('Error:', e);
  } finally {
    await browser.close();
  }
})();
