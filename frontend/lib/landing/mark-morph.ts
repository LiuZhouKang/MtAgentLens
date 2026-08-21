/**
 * Pure geometry for the landing hero's Zentry-style video-frame fold.
 *
 * `markClipPaths` / `markPathAt` — the hero's video-frame container folds away
 * as the user scrolls: it starts as a full-viewport rectangle and morphs into
 * the Zentry "tent" polygon (`polygon(14% 0, 72% 0, 88% 90%, 0 95%)`) — a top
 * band with two diagonals that cut away the frame's bottom-middle and
 * bottom-right. Because the hero-heading "M" letter sits in that bottom-right
 * corner, folding clips the in-frame M out of view and reveals the ghost twin
 * M layered at the exact same position beneath the frame (Zentry's layered
 * hero-heading "letter pushing through the frame"). `markBorderRadius` rounds
 * the bottom corners in the same motion (the `0% 0% 40% 10%` Zentry chamfer).
 *
 * Everything is pure so each piece is single-testable.
 */

export interface MarkClipPaths {
  /** Full-viewport rectangle — the initial (fully visible) clip. */
  initial: string;
  /** Zentry tent polygon — the scroll destination clip (bottom-right cut away). */
  final: string;
}

/** Clamp progress into [0, 1]. */
export function clampProgress(p: number): number {
  if (p <= 0) return 0;
  if (p >= 1) return 1;
  return p;
}

/** Interpolate a single coordinate between a and b by progress p ∈ [0,1]. */
function lerp(a: number, b: number, p: number): number {
  return a + (b - a) * p;
}

/** Fold a coordinate to 2 decimals — keeps CSS clip-path strings tidy. */
function fmt(n: number): string {
  return n.toFixed(2);
}

/**
 * Initial + final clip paths for a given viewport size.
 *
 * The tent geometry (fractions of the viewport, matching Zentry's fold):
 *   li   — x of the top-left vertex          (final: 0.14w)
 *   ri   — pull-in of the top-right vertex   (final: 0.28w, so x = 0.72w)
 *   cx   — x of the bottom-right vertex      (final: 0.88w)
 *   dyR  — y of the bottom-right vertex      (final: 0.90h)
 *   dyL  — y of the bottom-left vertex       (final: 0.95h)
 */
export function markClipPaths(width: number, height: number): MarkClipPaths {
  const li = width * 0.14;
  const ri = width * 0.28;
  const cx = width * 0.88;
  const dyR = height * 0.9;
  const dyL = height * 0.95;
  const initial = `M0 0 L${width} 0 L${width} ${height} L0 ${height} Z`;
  const final = `M${fmt(li)} 0 L${fmt(width - ri)} 0 L${fmt(cx)} ${fmt(dyR)} L0 ${fmt(dyL)} Z`;
  return { initial, final };
}

/**
 * Clip path at an arbitrary scroll progress p ∈ [0, 1] — linear interpolation
 * of each vertex between the rectangle and the tent. Lets a scrub drive the
 * morph via onUpdate instead of relying on GSAP's string interpolation.
 */
export function markPathAt(width: number, height: number, progress: number): string {
  const p = clampProgress(progress);
  if (p <= 0) {
    return `M0 0 L${width} 0 L${width} ${height} L0 ${height} Z`;
  }
  if (p >= 1) {
    return markClipPaths(width, height).final;
  }
  const li = lerp(0, width * 0.14, p);
  const rTop = lerp(width, width * 0.72, p);
  const rBot = lerp(width, width * 0.88, p);
  const rBotY = lerp(height, height * 0.9, p);
  const lBotY = lerp(height, height * 0.95, p);
  return `M${fmt(li)} 0 L${fmt(rTop)} 0 L${fmt(rBot)} ${fmt(rBotY)} L0 ${fmt(lBotY)} Z`;
}

/**
 * The same fold clip, wrapped for assignment to `element.style.clipPath`.
 *
 * CSS `clip-path` only accepts SVG path data inside a `path(...)` functional
 * notation with a quoted string; assigning a bare `M0 0 L… Z` path is silently
 * ignored by the browser (computed value falls back to `none`), which would
 * leave the mark permanently unfolded. Tests assert the wrapper.
 */
export function clipPathStyle(width: number, height: number, progress: number): string {
  return `path('${markPathAt(width, height, progress)}')`;
}

/**
 * Border-radius companion to the fold clip — the Zentry video-frame morph also
 * rounds the bottom corners as the frame folds away (`0% 0% 40% 10%`). Clamped
 * to [0, 1], monotonic, and frame-safe to assign inline.
 */
export function markBorderRadius(progress: number): string {
  const p = clampProgress(progress);
  return `0% 0% ${(40 * p).toFixed(2)}% ${(10 * p).toFixed(2)}%`;
}
