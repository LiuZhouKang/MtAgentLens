import { describe, expect, it } from 'vitest';
import { computeTilt, MAX_TILT_ANGLE } from '@/lib/landing/tilt';

// 100×100 卡片，rect 中心在 (50, 50)
const RECT = { left: 0, top: 0, width: 100, height: 100 };

describe('computeTilt — BentoTilt 3D 鼠标倾斜', () => {
  it('cursor at center → zero tilt, gentle scale', () => {
    const t = computeTilt({ x: 50, y: 50 }, RECT);
    expect(t.rotateX).toBe(0);
    expect(t.rotateY).toBe(0);
    expect(t.scale).toBeGreaterThan(1);
    expect(t.scale).toBeLessThanOrEqual(1.05);
  });

  it('top-left corner → max tilt both axes, clamped to ±MAX', () => {
    const t = computeTilt({ x: 0, y: 0 }, RECT);
    expect(Math.abs(t.rotateY)).toBe(MAX_TILT_ANGLE);
    expect(Math.abs(t.rotateX)).toBe(MAX_TILT_ANGLE);
  });

  it('bottom-right corner → opposite sign max tilt', () => {
    const t = computeTilt({ x: 100, y: 100 }, RECT);
    expect(t.rotateX).toBeCloseTo(-MAX_TILT_ANGLE, 5);
    expect(t.rotateY).toBeCloseTo(MAX_TILT_ANGLE, 5);
  });

  it('cursor outside the rect is clamped to max (no runaway tilt)', () => {
    const t = computeTilt({ x: 9999, y: -9999 }, RECT);
    expect(Math.abs(t.rotateY)).toBe(MAX_TILT_ANGLE);
    expect(Math.abs(t.rotateX)).toBe(MAX_TILT_ANGLE);
  });

  it('cursor right of center tilts toward the right (positive rotateY)', () => {
    const t = computeTilt({ x: 75, y: 50 }, RECT);
    expect(t.rotateY).toBeGreaterThan(0);
    expect(t.rotateX).toBe(0);
  });

  it('is deterministic', () => {
    expect(computeTilt({ x: 25, y: 75 }, RECT)).toEqual(computeTilt({ x: 25, y: 75 }, RECT));
  });

  it('handles zero-size rect without NaN', () => {
    const t = computeTilt({ x: 10, y: 10 }, { left: 0, top: 0, width: 0, height: 0 });
    expect(Number.isNaN(t.rotateX)).toBe(false);
    expect(Number.isNaN(t.rotateY)).toBe(false);
  });
});
