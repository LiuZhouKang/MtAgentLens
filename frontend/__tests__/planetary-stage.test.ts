import { describe, expect, it } from 'vitest';

describe('planetary stage helpers', () => {
  it('keeps globe outlines close to the surface', async () => {
    const { GLOBE_OUTLINE_RADIUS, GLOBE_RADIUS } = await import('@/engine/globe-renderer');

    expect(GLOBE_OUTLINE_RADIUS).toBeGreaterThan(GLOBE_RADIUS);
    expect(GLOBE_OUTLINE_RADIUS).toBeLessThan(GLOBE_RADIUS * 1.1);
  });

  it('starts intro particles away from their settled globe targets', async () => {
    const { generateGlobeSparkleCloud } = await import('@/engine/globe-sparkles');

    const cloud = generateGlobeSparkleCloud(6, 154, () => 0.5);

    expect(cloud.positions).toHaveLength(18);
    expect(cloud.targets).toHaveLength(18);
    expect(cloud.introOffsets).toHaveLength(6);

    for (let i = 0; i < 6; i++) {
      const initialY = cloud.positions[i * 3 + 1];
      const targetY = cloud.targets[i * 3 + 1];
      const initialZ = cloud.positions[i * 3 + 2];
      const targetZ = cloud.targets[i * 3 + 2];

      expect(initialY).toBeGreaterThan(targetY);
      expect(initialZ).toBeGreaterThan(targetZ);
    }
  });
});
