import { useEffect, type RefObject } from 'react';

/**
 * 顶级性能规范：视口外的持续 CSS 动画暂停（`animation-play-state: paused`），
 * 进入视口才恢复 —— 只渲染用户看得见的内容，滚动时主线程不被离屏重绘占满。
 *
 * 约定（与 globals.css 的 `:not(.anim-inview)` 暂停规则配合）：
 *  - 静态 markup 带 `anim-inview`（no-JS / SSR / 测试下动画照常跑，零回归）。
 *  - 本 hook 用 IntersectionObserver 按真实可见性增删该 class：
 *      section 离屏 → 移除 → CSS 暂停；进入视口 → 加回 → 恢复。
 *  - `rootMargin: '120px 0px'` 缓冲：即将进入视口时提前 120px 恢复，
 *    避免边缘处动画"刚停又启"的闪烁。
 *  - IntersectionObserver 不可用（SSR / 极老浏览器）→ 保持 class 不动，
 *    动画照常运行（优雅降级，绝不因优化而丢动画）。
 */
export function usePauseOffscreen(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(
      ([entry]) => el.classList.toggle('anim-inview', entry.isIntersecting),
      { rootMargin: '120px 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);
}
