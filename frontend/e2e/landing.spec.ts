import { test, expect, type Page } from '@playwright/test';

/**
 * 根路径 `/` — MtAgentLens 顶级 Landing 初始进入页 e2e。
 * 覆盖：六区块叙事渲染、进入观测台跳转（→ /console）、reduced-motion 静态降级、锚点导航。
 */

async function gotoWelcome(page: Page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
}

test.describe('landing 初始进入页 /', () => {
  test('renders the six-section narrative with real headings', async ({ page }) => {
    await gotoWelcome(page);

    // ① HERO — 巨型 h1 逐词标题 + 英文副行 + 滚动提示
    const hero = page.getByRole('heading', { level: 1 });
    await expect(hero).toBeVisible();
    // 惊艳 slogan 两行（全白）：大字 = 信任结论，小字 = 四层价值
    await expect(hero).toContainText('看得见');
    await expect(hero).toContainText('才信赖');
    await expect(page.locator('.landing-hero__sub')).toContainText('跨层可观测');
    // 专业副行（四层价值 slogan）+ 玻璃 CTA + 滚动提示
    await expect(page.locator('.landing-hero__sub')).toContainText('风险可预警');
    await expect(page.getByTestId('hero-cta')).toBeVisible();
    await expect(page.getByTestId('hero-scroll-hint')).toBeVisible();

    // ② 品牌时刻 — 巨型 wordmark（滚动入场，先滚到再断言）
    const brandHeading = page.getByRole('heading', { name: 'MtAgentLens', exact: true });
    await brandHeading.scrollIntoViewIfNeeded();
    await expect(brandHeading).toBeVisible();

    // ③ 三层管线 — 三个命名卡片 + 标签
    const layers: [string, string][] = [
      ['pipeline-card-ebpf', 'eBPF 采集层'],
      ['pipeline-card-rust', 'Rust 归因层'],
      ['pipeline-card-observe', '观测呈现层'],
    ];
    for (const [testId, name] of layers) {
      const card = page.getByTestId(testId);
      await card.scrollIntoViewIfNeeded();
      await expect(card).toBeVisible();
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    }

    // ④ 真实演示 — 责任图 SVG + 能力标签
    await expect(page.getByRole('heading', { name: '真实责任，即时归因' })).toBeVisible();
    await expect(page.getByTestId('demo-graph')).toBeVisible();
    await expect(page.getByRole('heading', { name: '为什么是 MtAgentLens' })).toBeVisible();

    // ⑤ 价值三支柱 — 01/02/03 编号
    await expect(page.getByText('01', { exact: true })).toBeVisible();

    // ⑥ CTA + 页脚
    await page.getByTestId('landing-cta').scrollIntoViewIfNeeded();
    await expect(page.getByTestId('landing-cta')).toBeVisible();
    await expect(page.getByText('MtAgentLens © 2026', { exact: false })).toBeVisible();
  });

  test('enters the console from the giant CTA', async ({ page }) => {
    await gotoWelcome(page);

    await page.getByTestId('landing-cta').scrollIntoViewIfNeeded();
    await page.getByTestId('landing-cta').click();

    // 跳到主界面「态势」（/console，渲染 CommandCenter）
    await page.waitForURL('**/console');
    await expect(page.getByRole('link', { name: '态势' })).toBeVisible();
  });

  test('nav has no redundant CTA; hero CTA enters the console (2026-08-10 去掉 nav 重复入口)', async ({
    page,
  }) => {
    await gotoWelcome(page);

    // nav 不再有 CTA（hero + 底部已承担「进入观测台」）
    await expect(page.getByTestId('landing-nav-cta')).toHaveCount(0);

    // hero 主 CTA 跳转 /console
    await page.getByTestId('hero-cta').click();
    await page.waitForURL('**/console');
    await expect(page.getByRole('link', { name: '态势' })).toBeVisible();
  });

  test('hero opens with the brand reveal on the black-hole video-frame — no particle layer', async ({
    page,
  }) => {
    await gotoWelcome(page);

    // 开场品牌时刻（LandingMarkReveal）—— MtlLogo 轨道标志 + wordmark 在场
    const reveal = page.getByTestId('landing-mark-reveal');
    await expect(reveal).toBeAttached();
    await expect(reveal.locator('.mtl-orbit').first()).toBeAttached();
    await expect(reveal.getByText('MtAgentLens')).toBeAttached();
    // 开场完成后品牌淡出消失 —— 只在开场展开，之后不再出现
    await expect.poll(() => reveal.evaluate((el) => getComputedStyle(el).display)).toBe('none');

    // 黑洞视频帧（data-testid="landing-video-frame"）
    const frame = page.getByTestId('landing-video-frame');
    await expect(frame).toBeVisible();
    // CC0 黑洞视频在场 —— 静音循环内联播放
    const video = page.locator('video.landing-mark__video');
    await expect(video).toBeVisible();
    await expect(video).toHaveAttribute('autoplay', '');
    await expect(video).toHaveAttribute('muted', '');
    await expect(video).toHaveAttribute('loop', '');
    // 黑洞视频资源真实可加载（防构建产物 404 → 静默空视频区）
    await expect
      .poll(() => page.request.get('/videos/hero-blackhole.mp4').then((r) => r.status()))
      .toBe(200);
    // 纯 CSS 深空背景在场（视频下方的静态兜底）
    await expect(page.locator('.landing-hero__bg')).toBeVisible();
    // 不再有常驻 M 图腾 / 孪生 M
    await expect(page.getByTestId('hero-letter')).toHaveCount(0);
    await expect(page.locator('.landing-hero__letter-ghost')).toHaveCount(0);
    // 全页无任何 canvas —— 黑洞视频帧纯粹干净，标题永不被遮挡
    await expect(page.locator('canvas')).toHaveCount(0);
  });

  test('keeps the black-hole hero fully visible under prefers-reduced-motion', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await gotoWelcome(page);

    // 降级：开场 M logo 不出现，视频帧 + 标题完整直达
    await expect(page.getByTestId('landing-mark-reveal')).toHaveCSS('display', 'none');
    await expect(page.getByTestId('landing-video-frame')).toBeVisible();
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    // 视频不动 —— 隐藏视频层，静态 scrim + 深空底接管
    await expect(page.locator('video.landing-mark__video')).toHaveCSS('display', 'none');
  });

  test('scrolling the hero folds the video-frame away via a valid clip-path', async ({ page }) => {
    await gotoWelcome(page);
    await expect(page.getByTestId('landing-video-frame')).toBeVisible();

    // 初始：开场动画结束后无 inline clip-path（完整矩形视频帧）
    const frame = page.locator('[data-testid="landing-video-frame"]');
    await expect.poll(() => frame.evaluate((el) => getComputedStyle(el).clipPath)).toBe('none');

    // 滚过 hero（.landing-root 自滚动容器）
    await page.evaluate(() => {
      const scroller = document.querySelector<HTMLElement>('.landing-root')!;
      scroller.scrollTop = 700;
    });

    // 浏览器接受的合法 path() 折角（裸 SVG path 会被忽略 → 回归保护）
    await expect.poll(() => frame.evaluate((el) => getComputedStyle(el).clipPath)).not.toBe('none');
    const clip = await frame.evaluate((el) => getComputedStyle(el).clipPath);
    expect(clip.startsWith('path(')).toBe(true);
    // Zentry video-frame morph 伴随项：底角圆角也在折叠中生效
    const radius = await frame.evaluate((el) => getComputedStyle(el).borderRadius);
    expect(radius).not.toBe('0px');
  });

  test('nav anchors smooth-scroll to sections', async ({ page }) => {
    await gotoWelcome(page);

    await page.getByRole('link', { name: '三层管线' }).click();
    await expect(page.getByRole('heading', { name: '一条管线，三层深度' })).toBeInViewport();

    // The nav auto-hides on scroll-down — scroll back to the top to reveal it
    // before following the next anchor (a real user scrolls up to re-open it).
    await page.evaluate(() => {
      const scroller = document.querySelector<HTMLElement>('.landing-root')!;
      scroller.scrollTop = 0;
    });
    await expect(page.getByTestId('landing-nav')).toBeVisible();

    await page.getByRole('link', { name: '真实演示' }).click();
    await expect(page.getByRole('heading', { name: '真实责任，即时归因' })).toBeInViewport();
  });
});
