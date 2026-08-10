/**
 * BentoTilt 3D tilt math (pure, deterministic).
 *
 * A card tilts toward the cursor: the pointer position relative to the card
 * center maps to rotateX/rotateY in degrees, clamped to ±MAX_TILT_ANGLE, with a
 * subtle scale lift. Consumed by the BentoTilt component.
 */

export const MAX_TILT_ANGLE = 10;

export interface TiltResult {
  rotateX: number;
  rotateY: number;
  scale: number;
}

export interface RectLike {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Clamp v into [-bound, bound]; NaN-safe (degenerate rect → 0). */
function clampSigned(v: number, bound: number): number {
  if (!Number.isFinite(v)) return 0;
  if (v > bound) return bound;
  if (v < -bound) return -bound;
  return v;
}

/**
 * Compute tilt from a cursor point (viewport coords) against a card rect.
 * - px/py ∈ [-1, 1]: cursor position normalized to the card, clamped.
 * - rotateY follows the cursor horizontally; rotateX inverts vertically so the
 *   card's near edge always lifts toward the pointer.
 */
export function computeTilt(point: { x: number; y: number }, rect: RectLike): TiltResult {
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const halfW = rect.width / 2 || 1; // degenerate rect → no division by zero
  const halfH = rect.height / 2 || 1;

  const px = clampSigned((point.x - cx) / halfW, 1);
  const py = clampSigned((point.y - cy) / halfH, 1);

  // Normalize -0 → 0 (Object.is(-0, 0) is false; tests and CSS both prefer 0).
  const rx = -py * MAX_TILT_ANGLE;
  const ry = px * MAX_TILT_ANGLE;

  return {
    rotateX: rx === 0 ? 0 : rx,
    rotateY: ry === 0 ? 0 : ry,
    scale: 1.02,
  };
}
