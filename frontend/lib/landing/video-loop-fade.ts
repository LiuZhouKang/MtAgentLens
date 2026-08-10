/**
 * Pure opacity schedule for seamlessly looping the hero's black-hole video.
 *
 * The Mixkit clip's first and last frames don't match — a hard `loop` cut
 * visibly jumps when the video restarts. Because the black hole is almost
 * entirely blackfield, fading the tail out to black and easing the head in
 * from black hides the seam entirely: the accretion disc "dissolves into
 * black, then re-emerges from black" on every cycle.
 *
 * The fade window is `fade` seconds on each side of the loop point:
 *   t ∈ [0, fade)      → opacity ramps 0 → 1  (re-emerging from black)
 *   t ∈ (duration-fade, duration] → opacity ramps 1 → 0 (dissolving to black)
 *   everything between → fully visible.
 *
 * Pure so it is single-testable; `LandingVideoFrame` drives it per frame from
 * a GSAP ticker and multiplies by the video's base CSS opacity.
 */
export function loopFadeOpacity(currentTime: number, duration: number, fade: number): number {
  // Metadata not ready yet — keep the video fully visible.
  if (!Number.isFinite(duration) || duration <= 0 || fade <= 0) return 1;
  if (currentTime < fade) return Math.min(1, currentTime / fade);
  if (duration - currentTime < fade) return Math.max(0, (duration - currentTime) / fade);
  return 1;
}
