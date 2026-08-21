# MtAgentLens 前端生产部署 Runbook

## 目标

将 `frontend` 从本地可运行状态推进到可上线、可回归、可监控的生产状态，并保证以下门槛：

- Lighthouse Performance / Accessibility / Best Practices >= 95
- LCP <= 2.5s
- INP <= 200ms
- CLS <= 0.1
- Chromium / Firefox / WebKit 关键路径可用

## 一、构建前准备

### 代码与质量

- 执行 `npm ci`
- 执行 `npm run audit:release`
- 在具备 WebKit 依赖的机器执行 `npm run audit:compat:webkit`
- 在稳定采集环境执行 `npm run audit:lighthouse`

### 环境变量

- `FRONTEND_MONITORING_ENDPOINT`
  - 指向正式前端监控采集端
  - 必须为 `https:` 地址
- `BASE_URL`
  - 在 E2E / Lighthouse 环境中指向待验证域名

## 二、边缘与网络配置

### CDN

- 对 `/_next/static/*` 开启 `cache-control: public, max-age=31536000, immutable`
- 对 `/globe/*` 开启长期缓存
- 对 `.js/.css/.svg/.png/.woff2` 开启长期缓存
- 对 `sw.js`、`manifest.webmanifest`、`offline.html` 保持 `must-revalidate`

### 传输

- 启用 HTTPS
- 启用 HTTP/3
- 启用 Brotli 与 gzip
- 为主域名开启 HSTS preload

## 三、安全策略

### 响应头

- 确认 `public/_headers` 在目标平台生效，或迁移到网关 / CDN 配置
- 验证以下头是否可见：
  - `Strict-Transport-Security`
  - `Content-Security-Policy`
  - `Cross-Origin-Opener-Policy`
  - `Cross-Origin-Resource-Policy`
  - `Permissions-Policy`
  - `Referrer-Policy`
  - `Origin-Agent-Cluster`

### CSP 收口

- 当前允许 `style-src 'unsafe-inline'`
- 当前允许 `script-src 'unsafe-inline'`
- 若后续进入更严格合规阶段，逐步迁移到 nonce / hash

## 四、浏览器验证矩阵

### 必测浏览器

- Chrome 最新稳定版
- Firefox 最新稳定版
- Safari / WebKit 最新稳定版
- Edge 最新稳定版

### 必测断点

- 320 x 568
- 375 x 667
- 768 x 1024
- 1024 x 768
- 1440 x 900
- 1920 x 1080
- 2560 x 1440 / 4K

### 必测路由

- `/`
- `/investigations?alertId=demo-alert-1`
- `/handoff`

## 五、监控与告警

- 接入 `FRONTEND_MONITORING_ENDPOINT`
- 验证以下事件成功上报：
  - `pageview`
  - `web-vital`
  - `interaction`
  - `error`
- 配置至少以下告警：
  - 前端错误突增
  - LCP / INP 持续超阈值
  - 主页面不可访问

## 六、最终发布门禁

- `npm run audit:release` 通过
- `npm run audit:compat:webkit` 在验证机通过
- Lighthouse 阈值在正式域名复核通过
- 生成并归档：
  - `frontend/.trae/reports/frontend-quality-report.md`
  - `frontend/.trae/reports/frontend-production-delivery-checklist.md`
  - `frontend/.trae/reports/frontend-production-runbook.md`

## 七、发布后回归

- 首页三维地球正常加载，headless 审计 fallback 不泄漏到真实用户路径
- 调查页无横向溢出、无裁切、无布局跳变
- Service Worker 可注册，离线页可兜底
- 关键 CTA、键盘导航、跳转链路、命令面板均可用
