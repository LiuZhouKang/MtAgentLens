# MtAgentLens 前端生产交付清单

## 边缘与网络

- 启用全球 CDN 分发，并对 `/_next/static/*`、`/globe/*`、静态图片、字体、脚本、样式开启长期缓存
- 在 CDN 或边缘平台启用 Brotli / gzip 压缩与 HTTP/3
- 为 `sw.js`、`manifest.webmanifest`、`offline.html` 保持 `must-revalidate`
- 在生产域名启用 HTTPS 与 HSTS preload

## 安全策略

- 确认部署平台支持 `public/_headers`，或将同等头策略迁移到网关 / CDN 配置
- 对正式域名验证 CSP、COOP、CORP、Permissions-Policy、Referrer-Policy 是否生效
- 对所有外部接入端点启用 TLS，仅通过 `https:` 上报监控数据
- 若后续引入动态内联脚本，优先迁移到 nonce / hash，逐步去除 `unsafe-inline`

## 性能验证

- 在正式域名跑 Lighthouse / PageSpeed / WebPageTest，确认 LCP、INP、CLS 达标
- 将监控端 `FRONTEND_MONITORING_ENDPOINT` 接入真实采集后端，校验页面性能、交互、错误上报链路
- 对首屏资源、关键图片、三维地球资源执行缓存命中率与回源压力验证

## 兼容性验证

- 在 CI 或专用验证机执行 Chromium / Firefox / WebKit 冒烟测试
- 对 iOS Safari、macOS Safari、Chrome、Firefox、Edge 的最新稳定版做最终回归
- 对 320px、375px、768px、1024px、1440px、1920px、4K 进行截图审计

## 发布门禁

- 通过 `npm run ci:quality`
- 通过 `npm run audit:compat`
- 在具备宿主依赖的环境通过 `npm run audit:compat:webkit`
- 在可稳定采集的环境通过 `npm run audit:lighthouse`
- 生成并归档 `frontend/.trae/reports/frontend-quality-report.md`
