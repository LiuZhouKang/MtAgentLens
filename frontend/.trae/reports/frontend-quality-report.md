# MtAgentLens 前端质量检测报告

更新时间：2026-06-20T14:16:30.864Z

## 本轮实测结果

- `npm run type-check`：通过
- `npm run test:coverage`：通过
- `npm run build`：通过
- `npm run audit:a11y`：通过
- `npm run audit:responsive`：通过
- `npm run audit:interaction`：通过，主题切换/调查切换/handoff 视图切换均在 100 ms 内完成 DOM 状态响应
- `npm run audit:theme`：通过
- `npm run audit:compat`：通过，Chromium + Firefox
- `npm run audit:compat:webkit`：当前宿主机缺少 Playwright WebKit 依赖，需在验证机或 CI 执行
- `npm run audit:lighthouse`：通过，已生成本地实测结果
- `npm run lint`：通过，当前发布范围内为 0 error / 0 warning

## 性能基线

- Lighthouse 性能阈值：95 / 100
- LCP 目标：<= 2000 ms
- INP 目标：<= 200 ms
- CLS 目标：<= 0.1
- FCP 目标：<= 1500 ms
- 预算文件：/

## Lighthouse 实测摘要

- `http://localhost:7395/?audit=headless`：性能 98 / 可访问性 100 / Best Practices 100 / SEO 100 / LCP 1131 ms / INP N/A / CLS 0.000 / FCP 291 ms
- `http://localhost:7395/investigations?alertId=demo-alert-1`：性能 97 / 可访问性 100 / Best Practices 100 / SEO 100 / LCP 1303 ms / INP N/A / CLS 0.000 / FCP 208 ms

## Lighthouse 告警摘要

- `http://localhost:7395/?audit=headless`：interaction-to-next-paint auditRan 未达预期，当前 0，目标 >= 1
- `http://localhost:7395/investigations?alertId=demo-alert-1`：interaction-to-next-paint auditRan 未达预期，当前 0，目标 >= 1

## 可访问性基线

- Lighthouse Accessibility 阈值：95 / 100
- 已启用键盘跳转入口：是
- 已启用 reduced motion 保护：是
- 已启用 Playwright 无障碍审计脚本：true

## 兼容性基线

- Chromium 冒烟验证：已配置
- Firefox 冒烟验证：已配置
- Edge 兼容性：沿用 Chromium 内核路径进行回归，建议在发布机补一次 Edge 实机冒烟
- WebKit 冒烟验证：已配置，CI 将执行；当前本地宿主机缺少依赖
- 多端响应式审计：true

## 安全基线

- HSTS：已配置
- CSP：已配置
- Frame Ancestors：已配置
- COOP：已配置
- Manifest 安装快捷入口：已配置
- 依赖高危告警：已清零
- 剩余依赖风险：`2 moderate`（`next -> postcss`）

## 自动化质量门禁

- 统一质量脚本：`npm run lint && npm run type-check && npm run test:coverage && npm run build && npm run audit:a11y && npm run audit:responsive && npm run audit:interaction && npm run audit:theme`
- 覆盖率门槛：lines 96.92% / statements 97.2% / functions 93.65% / branches 88.14%
- Lighthouse 审计：`npx -y @lhci/cli autorun --config=./lighthouserc.json`
- 交互时延审计：`playwright test e2e/interaction-latency.spec.ts --project=chromium --workers=1`
- 主题审计：`playwright test e2e/theme.spec.ts --project=chromium --workers=1`
- 兼容性审计：`playwright test e2e/compatibility-smoke.spec.ts --project=chromium --project=firefox --workers=1`
- WebKit 审计：`playwright test e2e/compatibility-smoke.spec.ts --project=webkit --workers=1`
- 一键发布审计：`npm run ci:quality && npm run audit:compat && npm run report:quality`
- 质量报告生成：`node ./scripts/generate-quality-report.mjs`

## 部署建议

- CDN / HTTP/3 / Brotli：需要在生产 CDN 或边缘平台启用
- 安全头：当前通过静态 `public/_headers` 下发，部署平台需确认支持
- 静态导出直达兼容：已为 `/handoff/` 与 `/investigations/` 生成 `index.html` 兼容层
- 监控上报：需要将 `FRONTEND_MONITORING_ENDPOINT` 指向正式采集端
- 生产部署步骤：参考 `frontend/.trae/reports/frontend-production-runbook.md`
