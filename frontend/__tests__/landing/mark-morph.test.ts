import { describe, expect, it } from 'vitest';
import {
  clampProgress,
  clipPathStyle,
  markBorderRadius,
  markClipPaths,
  markPathAt,
} from '@/lib/landing/mark-morph';

describe('mark-morph — 巨型 M 演绎纯函数', () => {
  it('produces an initial full-rectangle clip path from width/height', () => {
    const { initial } = markClipPaths(1000, 800);
    expect(initial).toBe('M0 0 L1000 0 L1000 800 L0 800 Z');
  });

  it('produces a final folded-geometry clip path that is a closed polygon', () => {
    const { final } = markClipPaths(1000, 800);
    expect(final.startsWith('M')).toBe(true);
    expect(final.trim().endsWith('Z')).toBe(true);
    // 顶边内收成「折角」：final 不是全屏矩形
    expect(final).not.toBe('M0 0 L1000 0 L1000 800 L0 800 Z');
    // 顶点全部落在画布内
    const nums = final.match(/-?\d+(\.\d+)?/g)?.map(Number) ?? [];
    expect(nums.length).toBeGreaterThanOrEqual(8);
    for (let i = 0; i < nums.length; i += 2) {
      expect(nums[i]).toBeGreaterThanOrEqual(0);
      expect(nums[i]).toBeLessThanOrEqual(1000);
      expect(nums[i + 1]).toBeGreaterThanOrEqual(0);
      expect(nums[i + 1]).toBeLessThanOrEqual(800);
    }
  });

  it('is responsive — different viewport sizes yield different paths', () => {
    expect(markClipPaths(1000, 800).initial).not.toBe(markClipPaths(1440, 900).initial);
    expect(markClipPaths(1000, 800).final).not.toBe(markClipPaths(1440, 900).final);
  });

  it('is deterministic — same size → same output', () => {
    expect(markClipPaths(1200, 700)).toEqual(markClipPaths(1200, 700));
  });

  it('clamps progress into [0, 1]', () => {
    expect(clampProgress(-0.5)).toBe(0);
    expect(clampProgress(1.5)).toBe(1);
    expect(clampProgress(0.42)).toBe(0.42);
  });

  it('interpolation is monotonic: progress 0 is the rectangle, 1 is the fold', () => {
    const { initial, final } = markClipPaths(1000, 800);
    expect(markPathAt(1000, 800, 0)).toBe(initial);
    expect(markPathAt(1000, 800, 1)).toBe(final);
    expect(markPathAt(1000, 800, 0.5)).not.toBe(initial);
    expect(markPathAt(1000, 800, 0.5)).not.toBe(final);
  });

  it('mid-progress interpolation yields a valid clip-path string', () => {
    const mid = markPathAt(1000, 800, 0.5);
    expect(mid.startsWith('M')).toBe(true);
    expect(mid.trim().endsWith('Z')).toBe(true);
  });

  it('clipPathStyle wraps the fold path in CSS path() notation', () => {
    // 裸 SVG path 会被浏览器忽略（computed 回退 none）——path() 包裹是必须的
    const style = clipPathStyle(1000, 800, 0.5);
    expect(style.startsWith("path('")).toBe(true);
    expect(style.endsWith("')")).toBe(true);
    expect(style).toContain(markPathAt(1000, 800, 0.5));
    // 包住的路径本身仍是闭合多边形
    expect(style).toContain('Z');
  });

  it('clipPathStyle tracks progress exactly like markPathAt', () => {
    expect(clipPathStyle(1000, 800, 0)).toBe(`path('${markPathAt(1000, 800, 0)}')`);
    expect(clipPathStyle(1000, 800, 1)).toBe(`path('${markPathAt(1000, 800, 1)}')`);
  });

  it('markBorderRadius morphs from square to the Zentry chamfer', () => {
    expect(markBorderRadius(0)).toBe('0% 0% 0.00% 0.00%');
    expect(markBorderRadius(1)).toBe('0% 0% 40.00% 10.00%');
  });

  it('final fold is the Zentry tent polygon — top band, diagonals cut the bottom-right', () => {
    // 4 顶点帐篷形：顶部两点 y=0，右下裁到 88%/90%，左下 95% —— 裁掉帧内 M 所在
    // 右下区域，露出帧外孪生 M（Zentry polygon(14% 0, 72% 0, 88% 90%, 0 95%)）
    expect(markClipPaths(1000, 800).final).toBe('M140.00 0 L720.00 0 L880.00 720.00 L0 760.00 Z');
  });

  it('markBorderRadius is monotonic and clamped', () => {
    expect(markBorderRadius(-0.5)).toBe('0% 0% 0.00% 0.00%');
    expect(markBorderRadius(2)).toBe('0% 0% 40.00% 10.00%');
    const mid = markBorderRadius(0.5);
    expect(mid).not.toBe(markBorderRadius(0));
    expect(mid).not.toBe(markBorderRadius(1));
  });
});
