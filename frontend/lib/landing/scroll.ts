/**
 * The landing page scrolls inside `.landing-root` — a viewport-height
 * `overflow-y: auto` container — not the window. The console's global
 * `html/body/#app-main { overflow: hidden }` rules leave the document itself
 * unscrollable, so a window-scoped ScrollTrigger would never fire.
 *
 * Pass `scroller: landingScroller()` on every landing ScrollTrigger so the
 * triggers track real user scrolls of that container. Falls back to the
 * document element when no `.landing-root` is mounted (unit tests, SSR shells)
 * so ScrollTrigger never receives an unresolvable target.
 */
export function landingScroller(): Element {
  return document.querySelector<HTMLElement>('.landing-root') ?? document.documentElement;
}
