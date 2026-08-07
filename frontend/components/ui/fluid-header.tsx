'use client';

import React, { useEffect, useRef, useState, useId } from 'react';
import webGLFluidEnhanced from 'webgl-fluid';

export const FLUID_PALETTES = {
  incident: ['#ff453a', '#ff9f0a', '#bf5af2', '#1c1c1e'], 
  entity: ['#0a84ff', '#5e5ce6', '#30d158', '#1c1c1e'],   
  evidence: ['#30d158', '#32ade6', '#000000', '#1c1c1e'], 
  narrative: ['#bf5af2', '#ff375f', '#000000', '#1c1c1e'], 
  delegation: ['#ffcc00', '#ff9500', '#ff3b30', '#1c1c1e'], 
};

export function FluidHeader({ 
  colors = FLUID_PALETTES.incident, 
  className,
  style 
}: { 
  colors?: string[],
  className?: string,
  style?: React.CSSProperties
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mounted, setMounted] = useState(false);
  const uniqueId = useId().replace(/:/g, '');

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || !canvasRef.current) return;
    
    const primaryHex = colors[0] || '#ff453a';
    const r = parseInt(primaryHex.slice(1, 3), 16) / 255;
    const g = parseInt(primaryHex.slice(3, 5), 16) / 255;
    const b = parseInt(primaryHex.slice(5, 7), 16) / 255;
    
    webGLFluidEnhanced(canvasRef.current, {
      TRIGGER: 'hover',       
      IMMEDIATE: true,        
      AUTO: false,            // 关闭原生鸡肋的闪烁 splat
      SPLAT_COLOR: { r, g, b }, 
      SIM_RESOLUTION: 128,    
      DYE_RESOLUTION: 512,    
      CAPTURE_RESOLUTION: 512,
      DENSITY_DISSIPATION: 1.5, // 让颜色存留更久一点
      VELOCITY_DISSIPATION: 0.5, // 让水流运动得更远更久
      PRESSURE: 0.8,
      PRESSURE_ITERATIONS: 20,
      CURL: 25,               // 增加涡流卷曲度，让流体更生动
      SPLAT_RADIUS: 0.5,     
      SPLAT_FORCE: 6000,
      SHADING: true,
      COLORFUL: false,        
      COLOR_UPDATE_SPEED: 10,
      PAUSED: false,
      BACK_COLOR: { r: 15, g: 15, b: 18 }, 
      TRANSPARENT: true,
      BLOOM: true,
      BLOOM_ITERATIONS: 8,
      BLOOM_RESOLUTION: 256,
      BLOOM_INTENSITY: 0.8,
      BLOOM_THRESHOLD: 0.6,
      BLOOM_SOFT_KNEE: 0.7,
      SUNRAYS: false,         
      SUNRAYS_RESOLUTION: 196,
      SUNRAYS_WEIGHT: 1.0,
    });

    const canvas = canvasRef.current;
    let frameId: number;
    let angle = 0;

    // "幽灵鼠标"：模拟一个鼠标在右侧区域（非遮罩区）缓慢画8字形，持续激起流体
    const simulateMouse = () => {
      if (!canvas) return;
      
      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) {
        frameId = requestAnimationFrame(simulateMouse);
        return;
      }

      angle += 0.01; // 运动速度
      
      // 让它在画布右侧区域游荡 (X: 50% 到 90%, Y: 20% 到 80%)
      const cx = rect.width * 0.7;
      const cy = rect.height * 0.5;
      const rx = rect.width * 0.2;
      const ry = rect.height * 0.3;

      const x = cx + Math.sin(angle) * rx;
      const y = cy + Math.sin(angle * 2) * ry;

      // 派发原生鼠标移动事件给 webgl-fluid 引擎
      const event = new MouseEvent('mousemove', {
        clientX: rect.left + x,
        clientY: rect.top + y,
        bubbles: true,
      });
      canvas.dispatchEvent(event);

      frameId = requestAnimationFrame(simulateMouse);
    };

    // 延迟启动幽灵鼠标，避免与初始化冲突
    const timeoutId = setTimeout(() => {
      simulateMouse();
    }, 1000);

    return () => {
       clearTimeout(timeoutId);
       cancelAnimationFrame(frameId);
    };
  }, [mounted, colors]);

  if (!mounted) return null;

  return (
    <div 
      className="fluid-gradient-wrapper"
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: '100%', 
        zIndex: 0,
        pointerEvents: 'none', 
        maskImage: 'radial-gradient(ellipse at 100% 0%, black 20%, transparent 80%)',
        WebkitMaskImage: 'radial-gradient(ellipse at 100% 0%, black 20%, transparent 80%)',
      }}
    >
      <canvas 
        id={'fluid-canvas-' + uniqueId}
        ref={canvasRef} 
        className={className}
        style={{
          ...style,
          width: '100%',
          height: '100%',
          pointerEvents: 'auto', 
        } as React.CSSProperties} 
      />
    </div>
  );
}
