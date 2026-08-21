import { describe, expect, it } from 'vitest';
import { loopFadeOpacity } from '@/lib/landing/video-loop-fade';

/**
 * 黑洞视频循环淡化 —— 视频首尾帧不匹配，直接 loop 会在结尾→开头处硬切。
 * `loopFadeOpacity` 把循环点前后各 `fade` 秒处理成淡出到黑 / 从黑淡入：
 * 黑洞素材以黑场为主，纯黑瞬间正好遮蔽跳变 —— 吸积盘「溶解进黑 → 从黑浮现」。
 */
describe('loopFadeOpacity — 视频循环淡化', () => {
  const FADE = 1.2;
  const DURATION = 8;

  it('starts at black and eases in from the loop point', () => {
    expect(loopFadeOpacity(0, DURATION, FADE)).toBe(0); // 跳回瞬间纯黑
    expect(loopFadeOpacity(0.6, DURATION, FADE)).toBeCloseTo(0.5);
    expect(loopFadeOpacity(1.2, DURATION, FADE)).toBe(1); // 完全浮现
  });

  it('stays fully visible through the middle of the clip', () => {
    expect(loopFadeOpacity(4, DURATION, FADE)).toBe(1);
  });

  it('eases out into black before the clip ends', () => {
    expect(loopFadeOpacity(6.8, DURATION, FADE)).toBe(1); // 未到淡出窗
    expect(loopFadeOpacity(7.4, DURATION, FADE)).toBeCloseTo(0.5);
    expect(loopFadeOpacity(DURATION, DURATION, FADE)).toBe(0); // 结尾纯黑 —— 遮蔽跳变
  });

  it('stays within [0, 1] on every frame', () => {
    for (const t of [0.01, 0.5, 1.19, 1.21, 6.79, 7.9, 7.99]) {
      const op = loopFadeOpacity(t, DURATION, FADE);
      expect(op).toBeGreaterThanOrEqual(0);
      expect(op).toBeLessThanOrEqual(1);
    }
  });

  it('no-ops (opacity 1) until the duration is known', () => {
    expect(loopFadeOpacity(0, Number.NaN, FADE)).toBe(1);
    expect(loopFadeOpacity(0, Infinity, FADE)).toBe(1);
    expect(loopFadeOpacity(0, 0, FADE)).toBe(1);
    expect(loopFadeOpacity(0, -1, FADE)).toBe(1);
  });

  it('handles a fade window wider than the clip (short videos)', () => {
    // 视频比淡窗短：淡入淡出重叠，任意帧 opacity 仍在 [0,1]
    const op = loopFadeOpacity(1, 1.4, 1.2);
    expect(op).toBeGreaterThanOrEqual(0);
    expect(op).toBeLessThanOrEqual(1);
  });
});
