# MtAgentLens 顶级 Landing 页设计文档

日期：2026-08-09
状态：Design Approved
目标：为 MtAgentLens 打造「惊艳 · 顶级 · 可感知品牌风格」的初始进入页（Landing Page）

---

## 1. 背景与目标

用户要求一个「让用户感到非常惊艳、顶级、了解 MtAgentLens 风格」的初始进入页，并选定 **Zentry (https://zentry.com)** 为动效与气质锚点：将 Zentry 的顶级三维/滚动动效体验「完美迁移」，内容按 MtAgentLens 产品改造。

**核心决策（已获用户批准）：**
- **动效技术 100% 复刻** Zentry：ScrollTrigger clip-path 全屏变形、逐词文字、BentoTilt 3D 卡片、frame mask 几何转场、rollover 换字、巨型几何按钮。
- **品牌元素原创演绎**：不使用 Zentry 的 logo/素材（版权 + 品牌统一性）。用 MtAgentLens 自有符号 **MtlLogo「Observation Orbit」**（琥珀轨道 `#fbbf24→#f97316`）做 Zentry 式巨型品牌时刻。
- **视觉基调**：纯黑深空底 `#000108` + 琥珀火焰 accent + 蓝紫科技副色（呼应产品界面）。
- **内容改造**：叙事映射 Zentry 的「Hero 宣言 → 产品三支柱 → 价值三支柱 → CTA」，内容为 MtAgentLens 的「零插桩 LLM Agent 可观测性」。

**成功标准：**
1. `/` 全屏独立 Landing 初始进入页（态势控制台移至 `/console`）（无主界面顶栏/事件轨）。
2. 六区块滚动叙事完整、动效如丝般顺滑（ScrollTrigger scrub 60fps）。
3. `prefers-reduced-motion` 下完整降级为静态可读页。
4. 四道门禁全绿：`npm run type-check`、`npm run lint` 0 warnings、`npx vitest run`、`npm run build`。
5. e2e（chromium+firefox）验证 `/` 渲染、动效降级、进入控制台跳转、现有 `/` 等页面无回归。

---

## 2. Zentry 解剖 → 迁移映射

| Zentry 武器 | 技术实现 | MtAgentLens 落地 |
|---|---|---|
| Hero 全屏 clip-path 变形 | ScrollTrigger `scrub:true` 绑定 scroll → `clip-path: path('...')` SVG 贝塞尔（宽响应） | 全屏粒子宇宙随滚动「折叠」收敛 |
| 逐词文字动画 `.word` | 标题拆词，逐个滚动显现 | 巨型标语逐词点亮 |
| BentoTilt 卡片 | 鼠标位置 → perspective/rotateX/rotateY/scale | 三层管线 3D 卡片 |
| frame mask 转场 | 区块间几何蒙版过渡 | 叙事段落场景切换 |
| 叙事三段式 | Hero 定义 → 产品三支柱 → 价值三支柱 | 同构，内容改为三层管线 |
| 巨型几何按钮 `btnMain__shapePath` | 文字 + 几何 shape path 图标 + rollover 换字 | `ln-btn` 体系改造，琥珀渐变 |

---

## 3. 视觉系统

### 3.1 色彩 token（Landing 作用域 `.landing-root`）

```
--land-bg: #000108;              /* 深空底（与产品 --bg-deep 同源） */
--land-surface: rgba(255,255,255,0.03);
--land-surface-2: rgba(255,255,255,0.06);
--land-hairline: rgba(255,255,255,0.1);
--land-amber: #fbbf24;           /* 品牌琥珀（MtlLogo 主色） */
--land-amber-2: #f97316;         /* 品牌火焰（MtlLogo 渐变端） */
--land-violet: #8b7dff;          /* 产品蓝紫副色 */
--land-cyan: #6cc2ff;            /* 产品蓝 */
--land-text: #fafafa;
--land-text-2: #a6abb9;
--land-text-3: #62687a;
```

- **琥珀** = 品牌/signature（CTA、巨型 logo、价值强调）。
- **蓝紫** = 产品/数据（粒子、管线、责任图）。
- 配色原则：Zentry 的橙红气质由琥珀火焰继承；产品界面连续性由深空底 + 蓝紫继承。

### 3.2 字体与排印

- 沿用项目字体栈（系统 sans + `--ln-mono` JetBrains Mono）。
- 巨型标题：`clamp(56px, 12vw, 160px)`、字重 600-700、`letter-spacing: -0.04em`、`text-wrap: balance`（Zentry 式大词）。
- eyebrow/标签：mono、11-12px、`letter-spacing: 0.12em`、uppercase、`--land-text-3`。
- 正文：14-16px、`--land-text-2`、行高 1.6、`max-width: 60ch`。

### 3.3 几何语言

- clip-path 多边形/贝塞尔（hero 变形、frame mask）。
- 几何符号：三层管线卡片用**原创 SVG 几何徽标**（内核环 / 数据流箭头 / 视窗）——不用 Zentry 素材。
- 按钮：大号几何形状 + `rollover` 悬停换字（文案切换：「进入控制台」↔「OBSERVE」）。
- 区块编号 01/02/03 大号 mono。

---

## 4. 页面结构（六区块滚动叙事）

```
app/(landing)/  (route group，无 (main) layout)
└── page.tsx —— LandingPage 组装
```

### ① HERO —— 「OBSERVE · ATTRIBUTE」宣言
- **背景**：全屏 3D 粒子宇宙（three.quarks），粒子从底部「内核源」涌现并向上流动（语义：eBPF 采集的原始事件流涌入观测层）。粒子色：蓝紫渐变，个别琥珀高亮（数据中的「责任事件」）。
- **前景**：
  - mono eyebrow：`eBPF · RUST · NEXT.JS — 零插桩观测`
  - 巨型标题（逐词点亮）：`零插桩` / `看见 Agent 的责任流动`（或副行英文 `OBSERVE THE RESPONSIBILITY FLOW OF AGENTS`）
  - 滚动提示条：「向下滚动探索」+ 滚动指示器。
- **滚动动效**：ScrollTrigger `scrub:true` 将全屏粒子画面 `clip-path: path(...)` 从矩形变形为几何「折叠」形，同时粒子场减速/淡出；`useResponsivePath` 按宽度重算 path。
- **降级**：`reduced-motion` → 静态渐变背景 + 静态标题（无粒子、无 scroll 动画）。

### ② 品牌时刻 —— 巨型 MtlLogo
- 全屏居中巨型 MtlLogo（`size ≈ 40vh`，琥珀渐变，animated）。滚动进入时 orbit 轨道 dash 展开（GSAP timeline 控制 `.mtl-orbit` strokeDashoffset），核心呼吸。
- 巨型品牌名 `MtAgentLens` + 一句品牌宣言：「零插桩，看见每个 Agent 做了什么、为什么。」
- **性能**：MtlLogo 已有 GPU-composited 动画 + reduced-motion 保护，直接复用。

### ③ 产品三支柱 —— BentoTilt 3D 卡片
- 三张 3D 倾斜卡片（BentoTilt：鼠标 → perspective/rotateX/rotateY/scale），stagger 滚动进入。
- 内容（对应 zDATA/zAI/zTERMINAL，用**命名**而非编号——编号留给 ⑤ 价值三支柱，避免视觉重复）：
  - **eBPF 采集层** — 「内核级零插桩探针，拦截 SSL/TLS 明文与进程事件」· 标签 `tls_capture · browsertrace · stdiocap`
  - **Rust 归因层** — 「流式框架实时归因：责任图 · 污点追踪 · 身份血缘」· 标签 `ResponsibilityGraph · Runtime`
  - **观测呈现层** — 「Next.js 交互观测：责任图 · 取证链 · 告警调查 · 回放」· 标签 `Overview · Investigations · Traces`
- 卡片间数据流连接线（SVG 箭头：eBPF → Rust → 观测）。

### ④ 真实演示 —— 责任图演化 + 取证链
- 数据源：**静态内联快照**——从 `db-sample.sqlite` 提取的一个迷你责任图（3-5 节点：agent 污染源 → 传播 → 判定）固化在 `landing-copy.ts` 的 `demoGraph` 字段。Landing 不依赖 store/导入，独立于数据源生命周期。
- 滚动叙事：节点依次点亮 → 污染源高亮 → 责任链传播 → 归因判定（谁做了什么、为什么）。
- **语义**：Zentry 用视频展示产品；我们用「真实责任图」展示产品核心（Evil Martians「展示产品胜过描述」最佳实践）。
- 能力标签带：责任图 / 取证链 / 告警调查 / 图时间演化。

### ⑤ 价值三支柱（01/02/03）
- **01 零插桩** — 内核级采集，无需改一行代码 / SDK。
- **02 责任归因** — 谁在 Agent 协作中做了什么、为什么，权威判定。
- **03 取证链** — 证据关联、回溯时间线、可回放审计。
- 大号编号 + 大数字/短句，Zentry 式。

### ⑥ 世界入口 CTA
- 巨型几何按钮「进入控制台 →」（`ln-btn--primary` 琥珀渐变改造 + rollover 换字）。点击 `router.push('/')`。
- 按钮下方技术栈 mono 标签：`eBPF · Rust 2024 · Next.js 16 · React 19`。
- 页脚极简：`MtAgentLens © 2026 · MIT Agent 可观测性` + 品牌 logo 小号。

---

## 5. 组件架构

```
components/landing/
  landing-nav.tsx        # 顶部导航：logo + 区块锚点(rollover) + 进入控制台按钮；scroll 显隐
  landing-hero.tsx       # ① HERO：ParticleField + WordReveal + clip-path 变形
  landing-brand-moment.tsx # ② 巨型 MtlLogo + 品牌宣言
  landing-pipeline.tsx   # ③ 三层管线 BentoTilt 卡片 + 数据流连接线
  landing-live-demo.tsx  # ④ 真实责任图演示（滚动叙事）
  landing-values.tsx     # ⑤ 价值三支柱
  landing-cta.tsx        # ⑥ CTA + 页脚
  particle-field.tsx     # three.quarks 粒子宇宙（SSR:false，LOD，reduced-motion 降级）
  benti-tilt.tsx         # BentoTilt 3D 卡片（鼠标 tilt）
  word-reveal.tsx        # 逐词文字动画（SplitText 或手写 span 拆分）
  frame-mask.tsx         # 几何转场 wrapper（clip-path 蒙版）

lib/landing/
  hero-paths.ts          # 纯函数：clip-path path 生成（初态矩形 → 终态几何），宽响应
  landing-copy.ts        # 纯函数：全站文案数据结构
  particle-config.ts     # 纯函数：粒子宇宙配置（数量/颜色/速度，按设备分级）
  pipeline-symbols.tsx   # 原创 SVG 几何徽标（三管线）
```

**依赖方向**：`page.tsx` → 区块组件 → 通用组件（`benti-tilt`/`word-reveal`/`particle-field`）+ 纯函数层（`hero-paths`/`landing-copy`/`particle-config`）。纯函数层无 React/副作用，可单测。

---

## 6. 路由与集成

- **新增** `app/(landing)/layout.tsx` + `app/(landing)/page.tsx` → 路由 `/`（初始进入页；态势控制台 `app/(main)/page.tsx` 移至 `/console`）。
- **不改动** 现有 `/`（态势）、`/overview`、`/investigations`、`/traces` 及 `(main)/layout.tsx`、根 `layout.tsx`——保证 e2e 零回归。
- `(landing)/layout.tsx`：全屏深空底（`min-height: 100dvh; background: #000108`），不含主界面顶栏/事件轨/数据源选择器。
- 进入控制台：`landing-cta` 与 `landing-nav` 的按钮 `router.push('/')`。
- 深链：Landing 各区块带 `id`（`#hero`/`#pipeline`/`#demo`/`#values`），导航锚点平滑滚动（`scroll-behavior: smooth`，reduced-motion 下关闭）。
- 首屏体验：`/` 是**静态可渲染**（SSR 出骨架/静态标题），3D 粒子 `dynamic(..., { ssr: false })` 客户端挂载——避免 SSR 卡死。

---

## 7. 动效实现细节

### 7.1 HERO clip-path 变形
- `useResponsivePath` hook：`resize` 监听重算 path；返回 `{ initial, final }`。
- 初态：全屏矩形（`M0 0 L100vw 0 L100vw 100vh L0 100vh Z`，四角圆角化用贝塞尔）。
- 终态：几何「折叠」形（顶边向内收束成多边形/梯形，底边展开）——具体 path 方程见 `hero-paths.ts`（纯函数，单测锁定形状不变）。
- GSAP timeline：
  ```ts
  gsap.timeline({ scrollTrigger: { trigger: root, start: 'top top', end: 'bottom 40%', scrub: true } })
    .to(particleLayer, { clipPath: finalPath, ... })
  ```
- `prefers-reduced-motion` → 不建 ScrollTrigger，粒子层静态。

### 7.2 逐词文字
- `word-reveal.tsx`：把标题文本拆成 `<span class="word">`，滚动进入时 `gsap.fromTo(..., { y: '110%', opacity: 0 }, { y: 0, opacity: 1, stagger: 0.04 })` + `clipPath` 上滚蒙版。
- 拆分逻辑为纯函数（`splitIntoWords`）可单测。

### 7.3 BentoTilt
- `benti-tilt.tsx`：`onMouseMove` → `rotateX/rotateY`（基于光标相对卡片中心，max ±10°），`scale: 1.02`，`transform-style: preserve-3d`，`will-change: transform`。
- 纯函数 `computeTilt(mouse, rect)` 可单测。

### 7.4 粒子宇宙（three.quarks）
- 已装 `three@0.184` + `three.quarks@0.17`。
- 配置（`particle-config.ts` 纯函数按设备分级）：
  - `mobile`：≤1024px → 4k 粒子、无 bloom
  - `desktop`：>1024px → 12k 粒子、轻 bloom
  - `reduced-motion` → 不挂载 canvas，静态渐变
- 粒子从底部源点（y=-0.6）向上流动（速度 + 正弦偏移），颜色蓝紫渐变，按 fnv1a 确定性比例洒 2% 琥珀「责任事件」粒子。
- 卸载时 `dispose()` 几何/材质/渲染器，避免泄漏。
- 挂载用 `useEffect` + rAF 循环（GSAP ticker 可选），不依赖 ScrollTrigger。

### 7.5 frame mask / 区块转场
- `frame-mask.tsx`：区块进入时 `clip-path: inset(0 0 100% 0)` → `inset(0)` 过渡（GSAP ScrollTrigger scrub 或 once）。
- 用于 ②→③、③→④、④→⑤ 的段落切换。

### 7.6 导航显隐
- `landing-nav.tsx`：`useWindowScroll` 滚动方向 → GSAP `y` 位移（上滑隐藏，下滑显示），顶部渐变遮罩。

---

## 8. 性能与可访问性

- **LCP**：首屏只渲染静态标题 + 渐变背景；粒子 canvas 客户端挂载、低复杂度初始。`dynamic(..., { ssr: false, loading: 骨架 })`。
- **LOD**：粒子数量/效果按设备分级；canvas 在 `visibilitychange` 隐藏时暂停 rAF。
- **reduced-motion**：所有 ScrollTrigger/GSAP/粒子降级为静态；`scroll-behavior` 关闭。
- **语义**：`<section aria-labelledby>`；标题用真 `h1/h2`（不用 div 冒充）；粒子层 `aria-hidden`；CTA 真按钮/链接。
- **键盘**：区块锚点可 Tab 聚焦；CTA 可 Enter 触发。
- **对比度**：正文 `--land-text-2 (#a6abb9)` on `#000108` 对比度 > 7:1；琥珀 accent 只用于非正文强调。
- **测试**：e2e `a11y`（axe）跑 `/`（landing）与 `/console`（控制台）；`prefers-reduced-motion` 模拟跑静态降级断言。

---

## 9. 错误处理

- **three.quarks 加载失败 / WebGL 不可用** → `particle-field` 降级为 CSS 渐变背景（try/catch `WebGLRenderingContext` 创建，失败渲染静态层），不阻塞页面。
- **样例数据加载失败**（④ 真实演示）→ 降级为静态示意图（SVG 简化责任图 + 说明文案），不崩溃。
- **scroll 动画在低端机掉帧** → ScrollTrigger 可 `fastScrollEnd` / 降采样；不强制。
- 诚实：无静默失败——降级路径都有 `data-testid` 与文案，e2e 覆盖。

---

## 10. 测试计划（TDD）

### 单测（vitest，jsdom/node）
- `hero-paths.test.ts`：初/终态 path 形状、宽响应（不同 width 输出合法 `clipPath` 字符串）、纯函数无副作用。
- `particle-config.test.ts`：设备分级（mobile/desktop/reduced）数量与参数；`reduced-motion` 返回 `null`。
- `landing-copy.test.ts`：文案数据结构完整（六区块标题/正文/标签非空）、`demoGraph` 迷你责任图含 3-5 节点与责任链关系、无 placeholder。
- `word-reveal.test.tsx`：拆分函数正确切词；渲染 `span.word` 数量与文本一致。
- `benti-tilt.test.ts`：`computeTilt` 边界（光标在中心 → 0°；角落 → ±max）。

### 组件测试（jsdom）
- `landing-nav.test.tsx`：渲染区块锚点；点击 CTA 触发 `router.push('/')`（mock next/navigation）。
- `landing-cta.test.tsx`：进入控制台跳转；rollover 换字文案切换。
- `landing-hero.test.tsx`：SSR 安全（`typeof window` 守卫）；`reduced-motion` mock 下不挂载粒子（断言 `WebGL` mock 未被调用）。

### e2e（playwright chromium+firefox）
- `/` 渲染六区块标题（`getByRole('heading')`）。
- 进入控制台 → URL 变 `/`（态势渲染）。
- `prefers-reduced-motion: reduce` → 断言静态（无 canvas，标题可见）。
- 锚点导航 `#pipeline` → 滚动到对应区块。
- 回归：`/`、`/overview`、`/investigations`、`/traces` 仍正常（全量 e2e 现有套件）。

---

## 11. 实施顺序（writing-plans 输入）

1. **纯函数层 + 单测**（红绿）：`hero-paths` → `particle-config` → `landing-copy` → `splitIntoWords` → `computeTilt`。
2. **通用组件 + 组件测试**：`word-reveal` → `benti-tilt` → `frame-mask`。
3. **粒子宇宙**：`particle-field`（SSR 安全 + LOD + reduced-motion + 降级）。
4. **Landing 区块**：`landing-nav` → `landing-hero` → `landing-brand-moment` → `landing-pipeline` → `landing-live-demo` → `landing-values` → `landing-cta`。
5. **路由集成**：`(landing)/layout` + `page` → `/`（初始进入页）；`(main)/page.tsx`（态势）移至 `/console`；四道门禁。
6. **e2e**：`e2e/landing.spec.ts`；全量回归。
7. **审查**：code-reviewer 轻量审查；用户验收。

---

## 12. 非目标（YAGNI）

- 不做多语言（i18n）。
- 不做 Landing 内置数据导入（进入控制台后由主界面承担）。
- 不迁移 Zentry 的 zDATA/zAI/zTERMINAL 文案或素材（版权 + 品牌）。
- Landing 即根路径 `/`（顶级初始进入页），「进入控制台」CTA 与主界面导航「态势」指向 `/console`，控制台其余页面不变。
- 不新增重型依赖（粒子用已装 three.quarks；动画用已装 gsap）。
