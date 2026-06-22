// ═══════════════════════════════════════════════════════════════
// Theme Toggle — Frontend ↔ Backend API Contract
// 版本: 1.0.0
// 日期: 2026-06-08
// ═══════════════════════════════════════════════════════════════
//
// 本文档定义了 ThemeToggle 前端组件与后端 API 之间的数据契约。
// 前后端均已实现并通过全部测试。
//
// ═══════════════════════════════════════════════════════════════

// ── API 端点 ─────────────────────────────────────────────────

/**
 * GET /api/user/preferences
 *
 * 描述: 获取当前用户的主题偏好。
 * 认证: 当前无认证层，使用 "default" 用户 ID。
 *       未来扩展: 支持 per-authenticated-user 偏好。
 *
 * 响应 200:
 *   { theme: "light" | "dark" | "system" }
 *
 * 默认行为: 当用户无已保存偏好时，返回 `{ theme: "system" }`。
 *          前端负责将 "system" 解析为 prefers-color-scheme 的实际值。
 */
export interface GetPreferencesResponse {
  theme: 'light' | 'dark' | 'system';
}

/**
 * PUT /api/user/preferences
 *
 * 描述: 更新当前用户的主题偏好（upsert 语义）。
 * Content-Type: application/json
 *
 * 请求体:
 *   { theme: "light" | "dark" | "system" }
 *
 * 成功响应 200:
 *   { success: true }
 *
 * 错误响应 400:
 *   { code: "invalid_theme", message: "..." }
 *   — 当 theme 值不在允许列表中时返回
 *
 * 错误响应 400:
 *   { code: "invalid_request", message: "..." }
 *   — 当请求体 JSON 解析失败时返回
 *
 * 错误响应 500:
 *   { code: "internal", message: "..." }
 *   — 数据库不可用时返回
 */
export interface PutPreferencesRequest {
  theme: 'light' | 'dark' | 'system';
}

export interface PutPreferencesResponse {
  success: true;
}

export interface ApiErrorResponse {
  code: string;
  message: string;
}

// ── 有效值 ──────────────────────────────────────────────────

/** 允许的主题值（前端和后端共享此约束） */
export const VALID_THEMES = ['light', 'dark', 'system'] as const;

/** localStorage 键名 */
export const THEME_STORAGE_KEY = 'app-theme';

// ── 前端组件接口 ────────────────────────────────────────────

export interface ThemeToggleProps {
  /** 初始主题，默认 'system' */
  initialTheme?: 'light' | 'dark' | 'system';
  /** 主题变更回调，传入解析后的有效主题 ('light' | 'dark') */
  onChange?: (theme: 'light' | 'dark') => void;
}

// ── 数据流 ─────────────────────────────────────────────────
//
//  1. 页面加载: layout.tsx 中的内联脚本读取 localStorage['app-theme']
//     → 在首帧渲染前应用 dark class，防止白屏闪烁
//
//  2. 组件挂载: ThemeToggle 调用 GET /api/user/preferences
//     → 成功后以后端数据为准，更新 localStorage 和 DOM
//     → 失败则回退到 localStorage，再回退到 'system'
//
//  3. 用户切换: 点击按钮循环 light → dark → system
//     → 立即写入 localStorage['app-theme']
//     → 立即更新 document.documentElement.classList (dark 类)
//     → 异步 PUT /api/user/preferences（fire-and-forget）
//     → 调用 onChange 回调（如提供）
//
//  4. 系统主题变化: 当 theme === 'system' 时，
//     监听 matchMedia('(prefers-color-scheme: dark)') 的 change 事件
//     → 自动更新 dark class，不改变 localStorage 值
//
// ═══════════════════════════════════════════════════════════════
