import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.BASE_URL || 'http://localhost:7395';
const serverPort = new URL(baseURL).port || '7395';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // 重型页面（3D 引擎 / Sigma / 延迟模型计算）并发过多会拖垮 CPU 与 dev server，
  // 导致数据加载与 100ms 交互预算抖动。本地限制为 2 worker，CI 串行。
  workers: process.env.CI ? 1 : 2,
  reporter: 'list',
  webServer: {
    command: `node node_modules/next/dist/bin/next dev -p ${serverPort}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],
});
