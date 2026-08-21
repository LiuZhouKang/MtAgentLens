// Adapted from the official @sohumsuthar/liquid-glass generator:
// https://github.com/sohumsuthar/liquid-glass
// Source script: node_modules/@sohumsuthar/liquid-glass/scripts/generate-displacement-map.mjs
// The algorithm and displacement profile are preserved; only the output path is
// aligned to this repository so the asset can be committed and shipped reliably.

import sharp from 'sharp';
import fs from 'node:fs';
import path from 'node:path';

const root = process.cwd();

const SIZE = 512;
const RADIUS = 48;
const BEZEL = 48;
const CHANNEL_DEPTH = 127;

const surfaces = {
  squircle: (x) => Math.pow(1 - Math.pow(1 - x, 4), 0.25),
  circle: (x) => Math.sqrt(1 - Math.pow(1 - x, 2)),
};
const SURFACE = 'squircle';
const f = surfaces[SURFACE];

const delta = 0.001;
function fPrime(x) {
  const x1 = Math.max(0, x - delta);
  const x2 = Math.min(1, x + delta);
  return (f(x2) - f(x1)) / (x2 - x1);
}

function sdfRoundedRect(px, py, w, h, r) {
  const cx = w / 2;
  const cy = h / 2;
  const qx = Math.abs(px - cx) - cx + r;
  const qy = Math.abs(py - cy) - cy + r;
  const outsideDist = Math.hypot(Math.max(qx, 0), Math.max(qy, 0));
  const insideDist = Math.min(Math.max(qx, qy), 0);
  return outsideDist + insideDist - r;
}

function sdfGradient(px, py, w, h, r) {
  const e = 0.75;
  const dx = sdfRoundedRect(px + e, py, w, h, r) - sdfRoundedRect(px - e, py, w, h, r);
  const dy = sdfRoundedRect(px, py + e, w, h, r) - sdfRoundedRect(px, py - e, w, h, r);
  const mag = Math.hypot(dx, dy) || 1;
  return { x: dx / mag, y: dy / mag };
}

const SAMPLES = 127;
const dispLut = new Float32Array(SAMPLES + 1);
let maxDisp = 0;
for (let i = 0; i <= SAMPLES; i += 1) {
  const x = i / SAMPLES;
  const slope = fPrime(x);
  const displacement = slope / (1 + slope * slope);
  dispLut[i] = displacement;
  if (displacement > maxDisp) maxDisp = displacement;
}
for (let i = 0; i <= SAMPLES; i += 1) {
  dispLut[i] /= maxDisp;
}

function sampleDispLut(t) {
  const index = Math.min(SAMPLES, Math.max(0, Math.floor(t * SAMPLES)));
  return dispLut[index];
}

async function generate() {
  const buffer = Buffer.alloc(SIZE * SIZE * 4);

  for (let py = 0; py < SIZE; py += 1) {
    for (let px = 0; px < SIZE; px += 1) {
      const sdf = sdfRoundedRect(px + 0.5, py + 0.5, SIZE, SIZE, RADIUS);
      const dist = -sdf;
      const idx = (py * SIZE + px) * 4;
      let r = 128;
      let g = 128;

      if (dist > 0 && dist < BEZEL) {
        const t = dist / BEZEL;
        const mag = sampleDispLut(t);
        const n = sdfGradient(px + 0.5, py + 0.5, SIZE, SIZE, RADIUS);
        const dx = -n.x * mag;
        const dy = -n.y * mag;

        r = Math.round(128 + dx * CHANNEL_DEPTH);
        g = Math.round(128 + dy * CHANNEL_DEPTH);
        r = Math.max(0, Math.min(255, r));
        g = Math.max(0, Math.min(255, g));
      }

      buffer[idx] = r;
      buffer[idx + 1] = g;
      buffer[idx + 2] = 128;
      buffer[idx + 3] = 255;
    }
  }

  const outDir = path.join(root, 'public', 'static', 'images');
  fs.mkdirSync(outDir, { recursive: true });
  const outPath = path.join(outDir, 'lg-displacement.png');

  await sharp(buffer, { raw: { width: SIZE, height: SIZE, channels: 4 } })
    .png({ compressionLevel: 9 })
    .toFile(outPath);

  console.log(`Generated ${outPath}`);
}

generate().catch((error) => {
  console.error(error);
  process.exit(1);
});
