'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

type RuntimeStageSignalFieldProps = {
  className?: string;
  intense?: boolean;
};

type Point = {
  x: number;
  y: number;
  wave: { x: number; y: number };
  cursor: { x: number; y: number; vx: number; vy: number };
};

type MouseState = {
  x: number;
  y: number;
  lx: number;
  ly: number;
  sx: number;
  sy: number;
  v: number;
  vs: number;
  a: number;
  set: boolean;
};

class Grad {
  constructor(
    readonly x: number,
    readonly y: number,
    readonly z: number,
  ) {}

  dot2(x: number, y: number) {
    return this.x * x + this.y * y;
  }
}

class PerlinNoise {
  private readonly grad3 = [
    new Grad(1, 1, 0),
    new Grad(-1, 1, 0),
    new Grad(1, -1, 0),
    new Grad(-1, -1, 0),
    new Grad(1, 0, 1),
    new Grad(-1, 0, 1),
    new Grad(1, 0, -1),
    new Grad(-1, 0, -1),
    new Grad(0, 1, 1),
    new Grad(0, -1, 1),
    new Grad(0, 1, -1),
    new Grad(0, -1, -1),
  ];

  private readonly p = [
    151, 160, 137, 91, 90, 15, 131, 13, 201, 95, 96, 53, 194, 233, 7, 225, 140, 36, 103, 30, 69,
    142, 8, 99, 37, 240, 21, 10, 23, 190, 6, 148, 247, 120, 234, 75, 0, 26, 197, 62, 94, 252, 219,
    203, 117, 35, 11, 32, 57, 177, 33, 88, 237, 149, 56, 87, 174, 20, 125, 136, 171, 168, 68, 175,
    74, 165, 71, 134, 139, 48, 27, 166, 77, 146, 158, 231, 83, 111, 229, 122, 60, 211, 133, 230,
    220, 105, 92, 41, 55, 46, 245, 40, 244, 102, 143, 54, 65, 25, 63, 161, 1, 216, 80, 73, 209, 76,
    132, 187, 208, 89, 18, 169, 200, 196, 135, 130, 116, 188, 159, 86, 164, 100, 109, 198, 173, 186,
    3, 64, 52, 217, 226, 250, 124, 123, 5, 202, 38, 147, 118, 126, 255, 82, 85, 212, 207, 206, 59,
    227, 47, 16, 58, 17, 182, 189, 28, 42, 223, 183, 170, 213, 119, 248, 152, 2, 44, 154, 163, 70,
    221, 153, 101, 155, 167, 43, 172, 9, 129, 22, 39, 253, 19, 98, 108, 110, 79, 113, 224, 232, 178,
    185, 112, 104, 218, 246, 97, 228, 251, 34, 242, 193, 238, 210, 144, 12, 191, 179, 162, 241, 81,
    51, 145, 235, 249, 14, 239, 107, 49, 192, 214, 31, 181, 199, 106, 157, 184, 84, 204, 176, 115,
    121, 50, 45, 127, 4, 150, 254, 138, 236, 205, 93, 222, 114, 67, 29, 24, 72, 243, 141, 128, 195,
    78, 66, 215, 61, 156, 180,
  ];

  private readonly perm = new Array<number>(512);
  private readonly gradP = new Array<Grad>(512);

  constructor(seed = 0) {
    this.seed(seed);
  }

  private seed(seed: number) {
    let normalized = seed;
    if (normalized > 0 && normalized < 1) normalized *= 65536;
    normalized = Math.floor(normalized);
    if (normalized < 256) normalized |= normalized << 8;

    for (let index = 0; index < 256; index += 1) {
      const value =
        index & 1 ? this.p[index] ^ (normalized & 255) : this.p[index] ^ ((normalized >> 8) & 255);
      this.perm[index] = this.perm[index + 256] = value;
      this.gradP[index] = this.gradP[index + 256] = this.grad3[value % 12];
    }
  }

  private fade(value: number) {
    return value * value * value * (value * (value * 6 - 15) + 10);
  }

  private lerp(a: number, b: number, t: number) {
    return (1 - t) * a + t * b;
  }

  perlin2(x: number, y: number) {
    let localX = x;
    let localY = y;
    let cellX = Math.floor(localX);
    let cellY = Math.floor(localY);
    localX -= cellX;
    localY -= cellY;
    cellX &= 255;
    cellY &= 255;

    const n00 = this.gradP[cellX + this.perm[cellY]].dot2(localX, localY);
    const n01 = this.gradP[cellX + this.perm[cellY + 1]].dot2(localX, localY - 1);
    const n10 = this.gradP[cellX + 1 + this.perm[cellY]].dot2(localX - 1, localY);
    const n11 = this.gradP[cellX + 1 + this.perm[cellY + 1]].dot2(localX - 1, localY - 1);
    const fadeX = this.fade(localX);

    return this.lerp(this.lerp(n00, n10, fadeX), this.lerp(n01, n11, fadeX), this.fade(localY));
  }
}

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const sync = () => setReduced(media.matches);
    sync();
    media.addEventListener('change', sync);
    return () => media.removeEventListener('change', sync);
  }, []);

  return reduced;
}

function joinClassNames(...values: Array<string | undefined | false>) {
  return values.filter(Boolean).join(' ');
}

function GridPulseLayer({ intense, reducedMotion }: { intense: boolean; reducedMotion: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const context = canvas.getContext('2d');
    if (!context) return undefined;

    let frameId = 0;
    let time = 0;
    let mouseX = -1000;
    let mouseY = -1000;
    const gridSize = intense ? 36 : 42;
    const speed = reducedMotion ? 0.18 : intense ? 1.8 : 1.1;
    const pulseRadius = intense ? 240 : 180;

    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const rect = parent.getBoundingClientRect();
      canvas.width = rect.width;
      canvas.height = rect.height;
    };

    const draw = () => {
      time += 0.02 * speed;
      context.clearRect(0, 0, canvas.width, canvas.height);

      for (let x = 0; x < canvas.width + gridSize; x += gridSize) {
        for (let y = 0; y < canvas.height + gridSize; y += gridSize) {
          const distFromMouse = Math.hypot(x - mouseX, y - mouseY);
          const mouseInfluence = Math.max(0, 1 - distFromMouse / pulseRadius);
          const centerX = canvas.width / 2;
          const centerY = canvas.height / 2;
          const distFromCenter = Math.hypot(x - centerX, y - centerY);
          const wave = reducedMotion ? 0.4 : Math.sin(distFromCenter * 0.018 - time) * 0.5 + 0.5;
          const intensity = Math.min(1, wave * 0.32 + mouseInfluence * 0.68);
          const dotSize = 1 + intensity * (intense ? 3.4 : 2.6);

          context.beginPath();
          context.arc(x, y, dotSize, 0, Math.PI * 2);
          context.fillStyle = `rgba(118, 215, 255, ${0.06 + intensity * 0.22})`;
          context.fill();

          if (x + gridSize < canvas.width) {
            context.beginPath();
            context.moveTo(x, y);
            context.lineTo(x + gridSize, y);
            context.strokeStyle = `rgba(77, 147, 199, ${0.02 + intensity * 0.07})`;
            context.lineWidth = 1;
            context.stroke();
          }

          if (y + gridSize < canvas.height) {
            context.beginPath();
            context.moveTo(x, y);
            context.lineTo(x, y + gridSize);
            context.strokeStyle = `rgba(77, 147, 199, ${0.02 + intensity * 0.07})`;
            context.lineWidth = 1;
            context.stroke();
          }
        }
      }

      if (!reducedMotion) frameId = window.requestAnimationFrame(draw);
    };

    const handleMouseMove = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseX = event.clientX - rect.left;
      mouseY = event.clientY - rect.top;
    };

    const handleMouseLeave = () => {
      mouseX = -1000;
      mouseY = -1000;
    };

    resize();
    draw();

    window.addEventListener('resize', resize);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseleave', handleMouseLeave);

    return () => {
      window.removeEventListener('resize', resize);
      canvas.removeEventListener('mousemove', handleMouseMove);
      canvas.removeEventListener('mouseleave', handleMouseLeave);
      if (frameId) window.cancelAnimationFrame(frameId);
    };
  }, [intense, reducedMotion]);

  return <canvas ref={canvasRef} className="runtime-stage-signal-field__grid" aria-hidden="true" />;
}

function WavesLayer({ intense, reducedMotion }: { intense: boolean; reducedMotion: boolean }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const contextRef = useRef<CanvasRenderingContext2D | null>(null);
  const boundsRef = useRef({ width: 0, height: 0, left: 0, top: 0 });
  const noiseRef = useRef(new PerlinNoise(Math.random()));
  const linesRef = useRef<Point[][]>([]);
  const frameRef = useRef(0);
  const mouseRef = useRef<MouseState>({
    x: -10,
    y: 0,
    lx: 0,
    ly: 0,
    sx: 0,
    sy: 0,
    v: 0,
    vs: 0,
    a: 0,
    set: false,
  });

  const config = useMemo(
    () => ({
      xGap: intense ? 14 : 18,
      yGap: intense ? 28 : 34,
      waveSpeedX: intense ? 0.014 : 0.009,
      waveSpeedY: intense ? 0.006 : 0.0036,
      waveAmpX: intense ? 20 : 12,
      waveAmpY: intense ? 11 : 8,
      friction: 0.92,
      tension: intense ? 0.005 : 0.004,
      maxCursorMove: intense ? 84 : 62,
    }),
    [intense],
  );

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return undefined;

    contextRef.current = canvas.getContext('2d');
    if (!contextRef.current) return undefined;

    const setSize = () => {
      const rect = container.getBoundingClientRect();
      boundsRef.current = {
        width: rect.width,
        height: rect.height,
        left: rect.left,
        top: rect.top,
      };
      canvas.width = rect.width;
      canvas.height = rect.height;
    };

    const setLines = () => {
      const { width, height } = boundsRef.current;
      linesRef.current = [];
      const totalLines = Math.ceil((width + 200) / config.xGap);
      const totalPoints = Math.ceil((height + 40) / config.yGap);
      const xStart = (width - config.xGap * totalLines) / 2;
      const yStart = (height - config.yGap * totalPoints) / 2;

      for (let line = 0; line <= totalLines; line += 1) {
        const points: Point[] = [];
        for (let point = 0; point <= totalPoints; point += 1) {
          points.push({
            x: xStart + config.xGap * line,
            y: yStart + config.yGap * point,
            wave: { x: 0, y: 0 },
            cursor: { x: 0, y: 0, vx: 0, vy: 0 },
          });
        }
        linesRef.current.push(points);
      }
    };

    const moved = (point: Point, withCursor = true) => ({
      x: Math.round((point.x + point.wave.x + (withCursor ? point.cursor.x : 0)) * 10) / 10,
      y: Math.round((point.y + point.wave.y + (withCursor ? point.cursor.y : 0)) * 10) / 10,
    });

    const drawLines = () => {
      const context = contextRef.current;
      if (!context) return;

      context.clearRect(0, 0, canvas.width, canvas.height);
      context.beginPath();
      context.strokeStyle = intense ? 'rgba(126, 235, 255, 0.17)' : 'rgba(126, 235, 255, 0.11)';
      context.lineWidth = 1;

      linesRef.current.forEach((points) => {
        let start = moved(points[0], false);
        context.moveTo(start.x, start.y);

        points.forEach((point, index) => {
          const isLast = index === points.length - 1;
          start = moved(point, !isLast && !reducedMotion);
          const next = moved(
            points[index + 1] ?? points[points.length - 1],
            !isLast && !reducedMotion,
          );
          context.lineTo(start.x, start.y);
          if (isLast) context.moveTo(next.x, next.y);
        });
      });

      context.stroke();
    };

    const movePoints = (time: number) => {
      linesRef.current.forEach((points) => {
        points.forEach((point) => {
          const offset =
            noiseRef.current.perlin2(
              (point.x + time * config.waveSpeedX) * 0.002,
              (point.y + time * config.waveSpeedY) * 0.0015,
            ) * 12;

          point.wave.x = Math.cos(offset) * config.waveAmpX;
          point.wave.y = Math.sin(offset) * config.waveAmpY;

          if (reducedMotion) {
            point.cursor.x = 0;
            point.cursor.y = 0;
            point.cursor.vx = 0;
            point.cursor.vy = 0;
            return;
          }

          const mouse = mouseRef.current;
          const dx = point.x - mouse.sx;
          const dy = point.y - mouse.sy;
          const dist = Math.hypot(dx, dy);
          const limit = Math.max(175, mouse.vs);

          if (dist < limit) {
            const strength = 1 - dist / limit;
            const force = Math.cos(dist * 0.001) * strength;
            point.cursor.vx += Math.cos(mouse.a) * force * limit * mouse.vs * 0.00065;
            point.cursor.vy += Math.sin(mouse.a) * force * limit * mouse.vs * 0.00065;
          }

          point.cursor.vx += (0 - point.cursor.x) * config.tension;
          point.cursor.vy += (0 - point.cursor.y) * config.tension;
          point.cursor.vx *= config.friction;
          point.cursor.vy *= config.friction;
          point.cursor.x += point.cursor.vx * 2;
          point.cursor.y += point.cursor.vy * 2;
          point.cursor.x = Math.min(
            config.maxCursorMove,
            Math.max(-config.maxCursorMove, point.cursor.x),
          );
          point.cursor.y = Math.min(
            config.maxCursorMove,
            Math.max(-config.maxCursorMove, point.cursor.y),
          );
        });
      });
    };

    const tick = (time: number) => {
      const mouse = mouseRef.current;
      mouse.sx += (mouse.x - mouse.sx) * 0.1;
      mouse.sy += (mouse.y - mouse.sy) * 0.1;
      const dx = mouse.x - mouse.lx;
      const dy = mouse.y - mouse.ly;
      const velocity = Math.hypot(dx, dy);
      mouse.v = velocity;
      mouse.vs += (velocity - mouse.vs) * 0.1;
      mouse.vs = Math.min(100, mouse.vs);
      mouse.lx = mouse.x;
      mouse.ly = mouse.y;
      mouse.a = Math.atan2(dy, dx);

      movePoints(time);
      drawLines();
      if (!reducedMotion) frameRef.current = window.requestAnimationFrame(tick);
    };

    const updateMouse = (clientX: number, clientY: number) => {
      const mouse = mouseRef.current;
      mouse.x = clientX - boundsRef.current.left;
      mouse.y = clientY - boundsRef.current.top;

      if (!mouse.set) {
        mouse.sx = mouse.x;
        mouse.sy = mouse.y;
        mouse.lx = mouse.x;
        mouse.ly = mouse.y;
        mouse.set = true;
      }
    };

    const onMouseMove = (event: MouseEvent) => updateMouse(event.clientX, event.clientY);
    const onTouchMove = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (!touch) return;
      updateMouse(touch.clientX, touch.clientY);
    };

    const onResize = () => {
      setSize();
      setLines();
      drawLines();
    };

    setSize();
    setLines();
    tick(0);

    window.addEventListener('resize', onResize);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('touchmove', onTouchMove, { passive: true });

    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('touchmove', onTouchMove);
      if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
    };
  }, [config, intense, reducedMotion]);

  return (
    <div ref={containerRef} className="runtime-stage-signal-field__waves" aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>
  );
}

function NoiseLayer() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const context = canvas.getContext('2d', { alpha: true });
    if (!context) return undefined;

    let frame = 0;
    let animationId = 0;
    const canvasSize = 256;

    const resize = () => {
      canvas.width = canvasSize;
      canvas.height = canvasSize;
    };

    const draw = () => {
      const image = context.createImageData(canvasSize, canvasSize);
      for (let index = 0; index < image.data.length; index += 4) {
        const value = Math.random() * 255;
        image.data[index] = value;
        image.data[index + 1] = value;
        image.data[index + 2] = value;
        image.data[index + 3] = 10;
      }
      context.putImageData(image, 0, 0);
    };

    const loop = () => {
      if (frame % 3 === 0) draw();
      frame += 1;
      animationId = window.requestAnimationFrame(loop);
    };

    resize();
    draw();
    loop();
    window.addEventListener('resize', resize);

    return () => {
      window.removeEventListener('resize', resize);
      if (animationId) window.cancelAnimationFrame(animationId);
    };
  }, []);

  return (
    <canvas ref={canvasRef} className="runtime-stage-signal-field__noise" aria-hidden="true" />
  );
}

export default function RuntimeStageSignalField({
  className,
  intense = false,
}: RuntimeStageSignalFieldProps) {
  const reducedMotion = usePrefersReducedMotion();

  return (
    <div className={joinClassNames('runtime-stage-signal-field', className)} aria-hidden="true">
      <GridPulseLayer intense={intense} reducedMotion={reducedMotion} />
      <WavesLayer intense={intense} reducedMotion={reducedMotion} />
      <NoiseLayer />
      <div className="runtime-stage-signal-field__glow" />
      <div className="runtime-stage-signal-field__vignette" />
    </div>
  );
}
