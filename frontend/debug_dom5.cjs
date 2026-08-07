const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rules = await page.evaluate(() => {
    const el = document.querySelector('.incident-panel-summary');
    if (!el) return 'Not found';
    
    const matchedRules = [];
    const stylesheets = document.styleSheets;
    for (let i = 0; i < stylesheets.length; i++) {
      let cssRules;
      try {
        cssRules = stylesheets[i].cssRules;
      } catch (e) {
        continue;
      }
      for (let j = 0; j < cssRules.length; j++) {
        const rule = cssRules[j];
        if (rule.type === CSSRule.STYLE_RULE) {
          try {
            if (el.matches(rule.selectorText)) {
              matchedRules.push(rule.selectorText + ' { ' + rule.style.cssText + ' }');
            }
          } catch (e) {
            // ignore pseudo elements
          }
        }
      }
    }
    return matchedRules;
  });
  
  console.log(JSON.stringify(rules, null, 2));
  await browser.close();
})();
