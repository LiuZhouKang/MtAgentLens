import { test, expect } from '@playwright/test';

/**
 * 主页面滚动可达性 —— 修复回归守卫。
 * 此前 #app-main/内容区 overflow:hidden 而 ln-page__inner 无滚动容器，
 * 仪表盘等主页面内容超出视口被裁剪、无法下滑。ln-page__inner 现为自滚动容器。
 */

test.describe('主页面滚动可达', () => {
  test.use({ viewport: { width: 1280, height: 600 } });

  test('overview：内容超出视口可滚动到底，折叠分节可达', async ({ page }) => {
    await page.goto('/overview', { waitUntil: 'domcontentloaded' });
    await page.locator('#app-main').waitFor();

    const scroller = page.locator('.ln-page__inner');
    await expect(scroller).toBeVisible();

    // 此前被 overflow:hidden 裁剪：现在应可滚动（内容高于视口）
    const scrollable = await scroller.evaluate((el) => el.scrollHeight > el.clientHeight);
    expect(scrollable).toBe(true);

    // 能滚动到底（下方内容可达）
    await scroller.evaluate((el) => el.scrollTo(0, el.scrollHeight));
    const atBottom = await scroller.evaluate(
      (el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 2,
    );
    expect(atBottom).toBe(true);

    // 折叠分节可用：展开「身份血缘」后内部视图可达
    await page.getByText('身份血缘').click();
    await expect(page.getByText(/进程谱系/i)).toBeVisible();
  });

  test('traces：长内容页同样可滚动', async ({ page }) => {
    await page.goto('/traces', { waitUntil: 'domcontentloaded' });
    await page.locator('#app-main').waitFor();

    const scroller = page.locator('.ln-page__inner');
    await expect(scroller).toBeVisible();
    const scrollable = await scroller.evaluate((el) => el.scrollHeight > el.clientHeight);
    expect(scrollable).toBe(true);
  });
});
