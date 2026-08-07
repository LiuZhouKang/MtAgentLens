const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  
  try {
    await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
    
    // Wait for the panel to be visible
    await page.waitForSelector('.incident-rail__panel-header', { timeout: 10000 });
    
    const styles = await page.evaluate(() => {
      const header = document.querySelector('.incident-rail__panel-header');
      if (!header) return null;
      const comp = window.getComputedStyle(header);
      
      const hero = document.querySelector('.incident-rail__panel-hero');
      const heroBefore = window.getComputedStyle(hero, '::before');
      
      const icon = document.querySelector('.incident-rail__panel-icon');
      const iconComp = window.getComputedStyle(icon);
      
      return {
        headerBackground: comp.backgroundColor,
        headerBorderBottom: comp.borderBottom,
        headerBackdropFilter: comp.backdropFilter,
        heroBeforeDisplay: heroBefore.display,
        iconBackground: iconComp.backgroundImage,
        iconBorderRadius: iconComp.borderRadius
      };
    });
    
    console.log(JSON.stringify(styles, null, 2));
  } catch (err) {
    console.error(err);
  } finally {
    await browser.close();
  }
})();
