const { chromium } = require('@playwright/test');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  await page.goto('http://localhost:7395', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.click('.incident-rail__navitem');
  await page.waitForTimeout(1000);
  
  const rules = await page.evaluate(() => {
    // Find all elements with a top or bottom border in the top 300px
    const elements = document.querySelectorAll('*');
    const result = [];
    for (const el of elements) {
      const rect = el.getBoundingClientRect();
      if (rect.top > 300) continue;
      const style = window.getComputedStyle(el);
      const borderTop = parseFloat(style.borderTopWidth);
      const borderBottom = parseFloat(style.borderBottomWidth);
      const bg = style.background;
      const shadow = style.boxShadow;
      if (borderTop > 0 || borderBottom > 0 || (shadow !== 'none' && shadow.includes('inset'))) {
        result.push({
          className: el.className,
          top: rect.top,
          bottom: rect.bottom,
          borderTop: style.borderTop,
          borderBottom: style.borderBottom,
          boxShadow: shadow
        });
      }
    }
    return result;
  });
  
  console.log(JSON.stringify(rules, null, 2));
  await browser.close();
})();
