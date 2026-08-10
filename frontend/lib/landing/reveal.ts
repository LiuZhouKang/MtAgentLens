/**
 * 顶级落地页 reveal 节奏（2026-08-10 滚动动态系统 scrub 化）。
 *
 * 之前：once 快速弹（0.5-0.6s）——滚动经过时内容"啪"地出现就静止，没有
 * 与滚动对话的顶级感。现在按 gsap-scrolltrigger 顶级规范升级为 **scrub 绑定**：
 * 内容随滚动进度平滑渐入（Linear/Vercel 手法）——快滚快现、慢滚慢现、
 * 回滚则淡出，滚动全程与内容对话。
 *
 * 关键参数（统一到所有 section）：
 *  - start 'top 92%'：section 顶部刚进入视口即开始渐入（不留给用户空白等待）
 *  - end   'top 55%'：滚到视口 55% 时完全呈现（约 37% 视口高度的滚动区间）
 *  - scrub 0.6：平滑跟随（数值 = 播放头追赶滚动的秒数）
 * 合成器友好（只动 transform/opacity），离屏区间 ScrollTrigger 不更新，性能可控。
 */
export const LANDING_REVEAL = {
  /** section 顶部刚进入视口即开始渐入 */
  start: 'top 92%',
  /** 滚到视口 55% 时完全呈现 */
  end: 'top 55%',
  /** 滚动平滑跟随 */
  scrub: 0.6,
  /** 标题逐词揭示时长（时间触发场景，如 hero） */
  wordRevealDuration: 0.6,
  /** 标题逐词 stagger 间隔（s） */
  wordRevealStagger: 0.025,
  /** 通用元素上升揭示时长（时间触发兜底） */
  itemDuration: 0.5,
} as const;
