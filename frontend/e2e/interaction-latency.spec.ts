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
        ? Array.from(document.querySelectorAll<HTMLElement>(selector)).find((node) =>
            node.textContent?.includes(expectedText),
          ) ?? null
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
  test('theme toggle responds within 100ms on the home page', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('app-theme', 'light');
    });
    await page.goto('/?audit=headless', { waitUntil: 'domcontentloaded' });
    const toggle = page.getByTestId('theme-toggle');
    await expect(toggle).toBeVisible();

    const duration = await measureInteractionLatency(
      page,
      '[data-testid="theme-toggle"]',
      `return document.documentElement.getAttribute('data-theme') === 'dark';`,
      async () => {
        await toggle.click();
      },
    );

    expect(duration).toBeLessThanOrEqual(100);
  });

  test('investigation alert switching responds within 100ms', async ({ page }) => {
    await page.goto('/investigations#alertId=demo-alert-1', { waitUntil: 'domcontentloaded' });

    const secondAlert = page.getByRole('button', { name: /Agent "frontend-expert" 正常操作/ });
    await expect(secondAlert).toBeVisible();

    const duration = await measureInteractionLatency(
      page,
      'button',
      `return window.location.hash.includes('demo-alert-2');`,
      async () => {
        await secondAlert.click();
      },
      'Agent "frontend-expert" 正常操作',
    );

    expect(duration).toBeLessThanOrEqual(100);
  });

  test('handoff tab switching responds within 100ms', async ({ page }) => {
    await page.goto('/handoff', { waitUntil: 'domcontentloaded' });

    const eventsTab = page.getByRole('button', { name: '事件流' });
    await expect(eventsTab).toBeVisible();

    const duration = await measureInteractionLatency(
      page,
      'button',
      `return Array.from(document.querySelectorAll('button')).some((node) => node.textContent?.includes('事件流') && node.className.includes('bg-indigo-600'));`,
      async () => {
        await eventsTab.click();
      },
      '事件流',
    );

    expect(duration).toBeLessThanOrEqual(100);
  });
});
