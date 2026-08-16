import { test, expect } from '@playwright/test';

/**
 * 主页面滚动可达性 —— 修复回归守卫。
 * 此前 #app-main/内容区 overflow:hidden 而 ln-page__inner 无滚动容器，
 * 仪表盘等主页面内容超出视口被裁剪、无法下滑。ln-page__inner 现为自滚动容器。
 */

// 等布局层全屏 Observation Orbit 覆盖淡出（协调式加载：覆盖到页面内容 + 侧边栏就绪），
// 再度量滚动——覆盖期间内容在 loader 下、高度仍在沉降，度量会误判为不可滚动。
async function waitForLoaderGone(page: import('@playwright/test').Page) {
  await page.waitForFunction(
    () => {
      for (const w of document.querySelectorAll('.mtl-load-word')) {
        let cur = w as HTMLElement | null;
        while (cur && cur !== document.body) {
          if (cur.getAttribute?.('aria-hidden') !== null) {
            if (getComputedStyle(cur).opacity !== '0') return false;
            break;
          }
          cur = cur.parentElement;
        }
      }
      return true;
    },
    undefined,
    { timeout: 20000 },
  );
}

test.describe('主页面滚动可达', () => {
  test.use({ viewport: { width: 1280, height: 600 } });

  test('overview：内容超出视口可滚动到底，折叠分节可达', async ({ page }) => {
    await page.goto('/overview', { waitUntil: 'domcontentloaded' });
    await page.locator('#app-main').waitFor();
    await waitForLoaderGone(page);

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

  test('traces：责任链路瀑布与事件详情正常渲染', async ({ page }) => {
    await page.goto('/traces', { waitUntil: 'domcontentloaded' });
    await page.locator('#app-main').waitFor();
    await waitForLoaderGone(page);

    await expect(page.getByText('责任链路', { exact: true })).toBeVisible();
    await expect(page.getByText('事件详情', { exact: true })).toBeVisible();
  });
});
