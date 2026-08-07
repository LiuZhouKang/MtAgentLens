import { expect, test } from '@playwright/test';

async function measureInteractionLatency(
  page: import('@playwright/test').Page,
  targetSelector: string,
  predicateSource: string,
  clickAction: () => Promise<void>,
  targetText?: string,
) {
  await page.evaluate(
    ({ selector, predicateBody, expectedText }) => {
      const trigger = expectedText
        ? (Array.from(document.querySelectorAll<HTMLElement>(selector)).find((node) =>
            node.textContent?.includes(expectedText),
          ) ?? null)
        : document.querySelector<HTMLElement>(selector);
      if (!trigger) {
        throw new Error(`Missing element for selector: ${selector}`);
      }

      const predicate = new Function(predicateBody) as () => boolean;
      const auditWindow = window as Window & {
        __mtagentInteractionAudit?: {
          done: boolean;
          duration: number | null;
          error: string | null;
        };
      };

      auditWindow.__mtagentInteractionAudit = {
        done: false,
        duration: null,
        error: null,
      };

      trigger.addEventListener(
        'click',
        () => {
          const start = performance.now();
          const deadline = start + 1000;

          const tick = () => {
            if (predicate()) {
              auditWindow.__mtagentInteractionAudit = {
                done: true,
                duration: performance.now() - start,
                error: null,
              };
              return;
            }

            if (performance.now() >= deadline) {
              auditWindow.__mtagentInteractionAudit = {
                done: true,
                duration: null,
                error: `Timed out waiting for predicate: ${selector}`,
              };
              return;
            }

            window.requestAnimationFrame(tick);
          };

          window.requestAnimationFrame(tick);
        },
        { once: true },
      );
    },
    { selector: targetSelector, predicateBody: predicateSource, expectedText: targetText },
  );

  await clickAction();

  await page.waitForFunction(() => {
    const auditWindow = window as Window & {
      __mtagentInteractionAudit?: {
        done: boolean;
        duration: number | null;
        error: string | null;
      };
    };

    return auditWindow.__mtagentInteractionAudit?.done === true;
  });

  const result = await page.evaluate(() => {
    const auditWindow = window as Window & {
      __mtagentInteractionAudit?: {
        done: boolean;
        duration: number | null;
        error: string | null;
      };
    };

    return auditWindow.__mtagentInteractionAudit ?? null;
  });

  if (!result || result.error || typeof result.duration !== 'number') {
    throw new Error(result?.error ?? `Missing interaction audit result for ${targetSelector}`);
  }

  return result.duration;
}

test.describe('interaction latency audit', () => {
  test('sidebar workbench switching responds within 100ms on the home page', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // 预热：先展开面板，度量稳态切换（冷启动展开会被首屏 3D 渲染/模型计算争抢主线程）
    const incidentBtn = page.getByRole('button', { name: '打开事件工作台' });
    await expect(incidentBtn).toBeVisible();
    await incidentBtn.click({ force: true });
    await expect(page.locator('.incident-rail__panel')).toBeVisible();

    const evidenceBtn = page.getByRole('button', { name: '打开证据工作台' });
    const duration = await measureInteractionLatency(
      page,
      '[aria-label="打开证据工作台"]',
      `return document.querySelector('.incident-rail')?.getAttribute('data-tool') === 'evidence';`,
      async () => {
        await evidenceBtn.click({ force: true });
      },
    );

    expect(duration).toBeLessThanOrEqual(100);
  });

  test('sidebar workbench switching responds within 100ms on the investigation page', async ({ page }) => {
    await page.goto('/investigations', { waitUntil: 'domcontentloaded' });

    // 预热：先展开面板，度量稳态切换（冷启动展开会被首屏模型计算争抢主线程）
    const incidentBtn = page.getByRole('button', { name: '打开事件工作台' });
    await expect(incidentBtn).toBeVisible();
    await incidentBtn.click({ force: true });
    await expect(page.locator('.incident-rail__panel')).toBeVisible();

    const traceBtn = page.getByRole('button', { name: '打开链路工作台' });
    const duration = await measureInteractionLatency(
      page,
      '[aria-label="打开链路工作台"]',
      `return document.querySelector('.incident-rail')?.getAttribute('data-tool') === 'trace';`,
      async () => {
        await traceBtn.click({ force: true });
      },
    );

    expect(duration).toBeLessThanOrEqual(100);
  });

});
