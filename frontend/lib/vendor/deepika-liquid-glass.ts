export type DeepikaLiquidGlassOptions = {
  scale?: number;
  chroma?: number;
  border?: number;
  mapBlur?: number;
  blur?: number;
  saturate?: number;
  radius?: number | null;
  fallbackBlur?: number;
};

type DeepikaLiquidGlassHandle = {
  supported: boolean;
  refresh: () => void;
  destroy: () => void;
};

const SVG_NS = 'http://www.w3.org/2000/svg';
let uid = 0;
let svgDefs: SVGDefsElement | null = null;

function setBackdropStyles(el: HTMLElement, value: string) {
  el.style.backdropFilter = value;
  el.style.setProperty('-webkit-backdrop-filter', value);
}

function clearBackdropStyles(el: HTMLElement) {
  el.style.backdropFilter = '';
  el.style.removeProperty('-webkit-backdrop-filter');
}

function isSupported() {
  if (typeof window === 'undefined' || typeof document === 'undefined') return false;
  const ua = window.navigator.userAgent;
  const isSafari = /Safari/.test(ua) && !/Chrome|Chromium|Edg/.test(ua);
  const isFirefox = /Firefox/.test(ua);
  if (isSafari || isFirefox) return false;
  if (!window.CSS?.supports?.('backdrop-filter', 'url(#lg)')) return false;

  try {
    const canvas = document.createElement('canvas');
    canvas.width = 4;
    canvas.height = 4;
    canvas.getContext('2d')?.getImageData(0, 0, 1, 1);
    return true;
  } catch {
    return false;
  }
}

function ensureDefs() {
  if (svgDefs) return svgDefs;

  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.setAttribute('aria-hidden', 'true');
  svg.style.position = 'absolute';

  svgDefs = document.createElementNS(SVG_NS, 'defs');
  svg.appendChild(svgDefs);
  document.body.appendChild(svg);
  return svgDefs;
}

function makeMap(w: number, h: number, radius: number, border: number, mapBlur: number) {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';

  const gx = ctx.createLinearGradient(0, 0, w, 0);
  gx.addColorStop(0, 'rgb(0,0,0)');
  gx.addColorStop(1, 'rgb(255,0,0)');
  ctx.fillStyle = gx;
  ctx.fillRect(0, 0, w, h);

  const gy = ctx.createLinearGradient(0, 0, 0, h);
  gy.addColorStop(0, 'rgb(0,0,0)');
  gy.addColorStop(1, 'rgb(0,0,255)');
  ctx.globalCompositeOperation = 'difference';
  ctx.fillStyle = gy;
  ctx.fillRect(0, 0, w, h);
  ctx.globalCompositeOperation = 'source-over';

  const inset = border * Math.min(w, h);
  ctx.filter = `blur(${mapBlur}px)`;
  ctx.fillStyle = 'rgba(128,128,128,0.93)';
  ctx.beginPath();
  ctx.roundRect(inset, inset, w - inset * 2, h - inset * 2, Math.max(radius - inset, 2));
  ctx.fill();
  ctx.filter = 'none';

  return canvas.toDataURL();
}

function buildFilter(id: string, scales: number[]) {
  const filter = document.createElementNS(SVG_NS, 'filter');
  filter.setAttribute('id', id);
  filter.setAttribute('x', '0');
  filter.setAttribute('y', '0');
  filter.setAttribute('width', '100%');
  filter.setAttribute('height', '100%');
  filter.setAttribute('color-interpolation-filters', 'sRGB');

  const feImage = document.createElementNS(SVG_NS, 'feImage');
  feImage.setAttribute('x', '0');
  feImage.setAttribute('y', '0');
  feImage.setAttribute('result', 'map');
  feImage.setAttribute('preserveAspectRatio', 'none');
  filter.appendChild(feImage);

  const keep = [
    '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0',
    '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0',
    '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0',
  ];

  const channels: string[] = [];
  for (let i = 0; i < 3; i += 1) {
    const disp = document.createElementNS(SVG_NS, 'feDisplacementMap');
    disp.setAttribute('in', 'SourceGraphic');
    disp.setAttribute('in2', 'map');
    disp.setAttribute('scale', String(scales[i]));
    disp.setAttribute('xChannelSelector', 'R');
    disp.setAttribute('yChannelSelector', 'B');
    disp.setAttribute('result', `d${i}`);
    filter.appendChild(disp);

    const cm = document.createElementNS(SVG_NS, 'feColorMatrix');
    cm.setAttribute('in', `d${i}`);
    cm.setAttribute('type', 'matrix');
    cm.setAttribute('values', keep[i]);
    cm.setAttribute('result', `c${i}`);
    filter.appendChild(cm);
    channels.push(`c${i}`);
  }

  const blend1 = document.createElementNS(SVG_NS, 'feBlend');
  blend1.setAttribute('in', channels[0]);
  blend1.setAttribute('in2', channels[1]);
  blend1.setAttribute('mode', 'screen');
  blend1.setAttribute('result', 'c01');
  filter.appendChild(blend1);

  const blend2 = document.createElementNS(SVG_NS, 'feBlend');
  blend2.setAttribute('in', 'c01');
  blend2.setAttribute('in2', channels[2]);
  blend2.setAttribute('mode', 'screen');
  filter.appendChild(blend2);

  ensureDefs().appendChild(filter);
  return { filter, feImage };
}

function resolveRadius(el: HTMLElement, w: number, h: number, override?: number | null) {
  if (override != null) return override;
  const raw = window.getComputedStyle(el).borderTopLeftRadius || '0px';
  const value = parseFloat(raw) || 0;
  return raw.trim().endsWith('%') ? (value / 100) * Math.min(w, h) : value;
}

export function applyDeepikaLiquidGlass(
  el: HTMLElement,
  opts: DeepikaLiquidGlassOptions = {},
): DeepikaLiquidGlassHandle {
  const options: Required<DeepikaLiquidGlassOptions> = {
    scale: opts.scale ?? -112,
    chroma: opts.chroma ?? 6,
    border: opts.border ?? 0.07,
    mapBlur: opts.mapBlur ?? 12,
    blur: opts.blur ?? 3,
    saturate: opts.saturate ?? 1.5,
    radius: opts.radius ?? null,
    fallbackBlur: opts.fallbackBlur ?? 16,
  };

  if (!isSupported()) {
    const frosted = `blur(${options.fallbackBlur}px) saturate(${options.saturate})`;
    setBackdropStyles(el, frosted);
    el.classList.add('lg-fallback');

    return {
      supported: false,
      refresh: () => {},
      destroy: () => {
        clearBackdropStyles(el);
        el.classList.remove('lg-fallback');
      },
    };
  }

  const id = `lg-filter-${++uid}`;
  const scales = [
    options.scale,
    options.scale + options.chroma,
    options.scale + 2 * options.chroma,
  ];
  const parts = buildFilter(id, scales);

  const refresh = () => {
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    if (!w || !h) return;

    const radius = resolveRadius(el, w, h, options.radius);
    parts.feImage.setAttribute('href', makeMap(w, h, radius, options.border, options.mapBlur));
    parts.feImage.setAttribute('width', String(w));
    parts.feImage.setAttribute('height', String(h));
  };

  refresh();
  const backdropValue = `url(#${id}) blur(${options.blur}px) saturate(${options.saturate})`;
  setBackdropStyles(el, backdropValue);

  let timer: ReturnType<typeof setTimeout> | null = null;
  const ro = new ResizeObserver(() => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(refresh, 120);
  });
  ro.observe(el);

  return {
    supported: true,
    refresh,
    destroy: () => {
      ro.disconnect();
      if (timer) clearTimeout(timer);
      parts.filter.remove();
      clearBackdropStyles(el);
    },
  };
}
