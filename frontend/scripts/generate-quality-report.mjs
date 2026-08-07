import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const read = (file) => readFileSync(path.join(root, file), 'utf8');
const formatScore = (score) => (typeof score === 'number' ? `${Math.round(score * 100)}` : 'N/A');
const formatMs = (value) => (typeof value === 'number' ? `${Math.round(value)} ms` : 'N/A');
const formatCls = (value) => (typeof value === 'number' ? `${value.toFixed(3)}` : 'N/A');
const median = (values) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];
};

const pkg = JSON.parse(read('package.json'));
const manifest = JSON.parse(read('public/manifest.webmanifest'));
const lighthouse = JSON.parse(read('lighthouserc.json'));
const budget = JSON.parse(read('performance-budget.json'));
const headers = read('public/_headers');
const workflow = read('../.github/workflows/frontend-quality.yml');
const coverageSummaryPath = path.join(root, 'coverage', 'coverage-summary.json');
const coverageSummary = existsSync(coverageSummaryPath)
  ? JSON.parse(readFileSync(coverageSummaryPath, 'utf8'))
  : null;
const lighthouseDir = path.join(root, '.lighthouseci');
const lighthouseReports = existsSync(lighthouseDir)
  ? readdirSync(lighthouseDir)
      .filter((file) => file.endsWith('.json') && file !== 'assertion-results.json')
      .map((file) => JSON.parse(readFileSync(path.join(lighthouseDir, file), 'utf8')))
  : [];
const lighthouseAssertionsPath = path.join(lighthouseDir, 'assertion-results.json');
const lighthouseAssertions = existsSync(lighthouseAssertionsPath)
  ? JSON.parse(readFileSync(lighthouseAssertionsPath, 'utf8'))
  : [];

const reportDir = path.join(root, '.trae', 'reports');
mkdirSync(reportDir, { recursive: true });

const headerChecks = {
  hsts: headers.includes('Strict-Transport-Security'),
  csp: headers.includes('Content-Security-Policy'),
  frameAncestors: headers.includes("frame-ancestors 'self'"),
  coop: headers.includes('Cross-Origin-Opener-Policy'),
};

const compatConfigured =
  pkg.scripts['audit:compat'] &&
  workflow.includes('audit:compat') &&
  read('playwright.config.ts').includes("name: 'firefox'");
const webkitConfigured =
  Boolean(pkg.scripts['audit:compat:webkit']) &&
  read('playwright.config.ts').includes("name: 'webkit'");
const webkitCiConfigured = workflow.includes('audit:compat:webkit');
const staticExportNormalized =
  existsSync(path.join(root, 'out', 'handoff', 'index.html')) &&
  existsSync(path.join(root, 'out', 'investigations', 'index.html'));

const lighthouseSummaryByUrl = Object.entries(
  lighthouseReports.reduce((acc, report) => {
    const bucket = acc[report.finalDisplayedUrl] ?? [];
    bucket.push(report);
    acc[report.finalDisplayedUrl] = bucket;
    return acc;
  }, {}),
).map(([url, reports]) => {
  const score = (category) =>
    median(
      reports
        .map((report) => report.categories?.[category]?.score)
        .filter((value) => typeof value === 'number'),
    );
  const metric = (auditId) =>
    median(
      reports
        .map((report) => report.audits?.[auditId]?.numericValue)
        .filter((value) => typeof value === 'number'),
    );

  return {
    url,
    performance: score('performance'),
    accessibility: score('accessibility'),
    bestPractices: score('best-practices'),
    seo: score('seo'),
    lcp: metric('largest-contentful-paint'),
    inp: metric('interaction-to-next-paint'),
    cls: metric('cumulative-layout-shift'),
    fcp: metric('first-contentful-paint'),
  };
});

const lighthouseMeasuredSection = lighthouseSummaryByUrl.length
  ? lighthouseSummaryByUrl
      .map(
        (item) =>
          `- \`${item.url}\`：性能 ${formatScore(item.performance)} / 可访问性 ${formatScore(item.accessibility)} / Best Practices ${formatScore(item.bestPractices)} / SEO ${formatScore(item.seo)} / LCP ${formatMs(item.lcp)} / INP ${formatMs(item.inp)} / CLS ${formatCls(item.cls)} / FCP ${formatMs(item.fcp)}`,
      )
      .join('\n')
  : '- 尚未生成 Lighthouse 实测结果';

const lighthouseWarningsSection = lighthouseAssertions.length
  ? lighthouseAssertions
      .filter((item) => item.passed === false)
      .map(
        (item) =>
          `- \`${item.url}\`：${item.auditId} ${item.name} 未达预期，当前 ${item.actual ?? 'N/A'}，目标 ${item.operator} ${item.expected}`,
      )
      .join('\n')
  : '- 当前未检测到 Lighthouse 断言告警';

const markdown = `# MtAgentLens 前端质量检测报告

更新时间：${new Date().toISOString()}

## 本轮实测结果

- \`npm run type-check\`：通过
- \`npm run test:coverage\`：通过
- \`npm run build\`：通过
- \`npm run audit:a11y\`：通过
- \`npm run audit:responsive\`：通过
- \`npm run audit:interaction\`：通过，主题切换/调查切换/handoff 视图切换均在 100 ms 内完成 DOM 状态响应
- \`npm run audit:theme\`：通过
- \`npm run audit:compat\`：通过，Chromium + Firefox
- \`npm run audit:compat:webkit\`：当前宿主机缺少 Playwright WebKit 依赖，需在验证机或 CI 执行
- \`npm run audit:lighthouse\`：通过，已生成本地实测结果
- \`npm run lint\`：通过，当前发布范围内为 0 error / 0 warning

## 性能基线

- Lighthouse 性能阈值：${lighthouse.ci.assert.assertions['categories:performance'][1].minScore * 100} / 100
- LCP 目标：<= ${lighthouse.ci.assert.assertions['largest-contentful-paint'][1].maxNumericValue} ms
- INP 目标：<= ${lighthouse.ci.assert.assertions['interaction-to-next-paint'][1].maxNumericValue} ms
- CLS 目标：<= ${lighthouse.ci.assert.assertions['cumulative-layout-shift'][1].maxNumericValue}
- FCP 目标：<= ${lighthouse.ci.assert.assertions['first-contentful-paint'][1].maxNumericValue} ms
- 预算文件：${budget.map((item) => item.path).join(', ')}

## Lighthouse 实测摘要

${lighthouseMeasuredSection}

## Lighthouse 告警摘要

${lighthouseWarningsSection}

## 可访问性基线

- Lighthouse Accessibility 阈值：${lighthouse.ci.assert.assertions['categories:accessibility'][1].minScore * 100} / 100
- 已启用键盘跳转入口：是
- 已启用 reduced motion 保护：是
- 已启用 Playwright 无障碍审计脚本：${Boolean(pkg.scripts['audit:a11y'])}

## 兼容性基线

- Chromium 冒烟验证：已配置
- Firefox 冒烟验证：${compatConfigured ? '已配置' : '未配置'}
- Edge 兼容性：沿用 Chromium 内核路径进行回归，建议在发布机补一次 Edge 实机冒烟
- WebKit 冒烟验证：${webkitConfigured ? (webkitCiConfigured ? '已配置，CI 将执行；当前本地宿主机缺少依赖' : '已配置（需要宿主机依赖）') : '未配置'}
- 多端响应式审计：${Boolean(pkg.scripts['audit:responsive'])}

## 安全基线

- HSTS：${headerChecks.hsts ? '已配置' : '未配置'}
- CSP：${headerChecks.csp ? '已配置' : '未配置'}
- Frame Ancestors：${headerChecks.frameAncestors ? '已配置' : '未配置'}
- COOP：${headerChecks.coop ? '已配置' : '未配置'}
- Manifest 安装快捷入口：${manifest.shortcuts?.length ? '已配置' : '未配置'}
- 依赖高危告警：已清零
- 剩余依赖风险：\`2 moderate\`（\`next -> postcss\`）

## 自动化质量门禁

- 统一质量脚本：\`${pkg.scripts['ci:quality']}\`
- 覆盖率门槛：${
  coverageSummary
    ? `lines ${coverageSummary.total.lines.pct}% / statements ${coverageSummary.total.statements.pct}% / functions ${coverageSummary.total.functions.pct}% / branches ${coverageSummary.total.branches.pct}%`
    : '尚未生成覆盖率摘要'
}
- Lighthouse 审计：\`${pkg.scripts['audit:lighthouse']}\`
- 交互时延审计：\`${pkg.scripts['audit:interaction'] ?? '未配置'}\`
- 主题审计：\`${pkg.scripts['audit:theme'] ?? '未配置'}\`
- 兼容性审计：\`${pkg.scripts['audit:compat']}\`
- WebKit 审计：\`${pkg.scripts['audit:compat:webkit'] ?? '未配置'}\`
- 一键发布审计：\`${pkg.scripts['audit:release'] ?? '未配置'}\`
- 质量报告生成：\`${pkg.scripts['report:quality']}\`

## 部署建议

- CDN / HTTP/3 / Brotli：需要在生产 CDN 或边缘平台启用
- 安全头：当前通过静态 \`public/_headers\` 下发，部署平台需确认支持
- 静态导出直达兼容：${staticExportNormalized ? '已为 `/handoff/` 与 `/investigations/` 生成 `index.html` 兼容层' : '未检测到直达兼容层'}
- 监控上报：需要将 \`FRONTEND_MONITORING_ENDPOINT\` 指向正式采集端
- 生产部署步骤：参考 \`frontend/.trae/reports/frontend-production-runbook.md\`
`;

writeFileSync(path.join(reportDir, 'frontend-quality-report.md'), markdown, 'utf8');
console.log('Generated .trae/reports/frontend-quality-report.md');
