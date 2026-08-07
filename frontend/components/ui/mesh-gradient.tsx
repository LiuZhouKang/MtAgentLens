'use client';
import React, { useEffect, useRef, useState, useId } from 'react';
import { Gradient } from 'whatamesh';

// 顶级大厂 Apple Pro / Stripe 风格的 5 大系统调色板 (Dark Mode 优化)
export const MESH_PALETTES = {
  // 事件工作台 (Incident) - 危险、紧急、高能 (猩红/亮橙/深紫)
  incident: ['#ff453a', '#ff9f0a', '#bf5af2', '#1c1c1e'] as [string, string, string, string],
  // 主体工作台 (Entity) - 身份、角色、权限 (深海蓝/靛青/紫罗兰)
  entity: ['#0a84ff', '#5e5ce6', '#30d158', '#1c1c1e'] as [string, string, string, string],
  // 证据工作台 (Evidence) - 数据、溯源、事实 (极光绿/青色/深邃灰)
  evidence: ['#30d158', '#32ade6', '#000000', '#1c1c1e'] as [string, string, string, string],
  // 叙事/时间线 (Narrative) - 故事、发展、时间 (紫水晶/玫瑰红/深黑)
  narrative: ['#bf5af2', '#ff375f', '#000000', '#1c1c1e'] as [string, string, string, string],
  // 委派/网络 (Delegation) - 拓扑、流转、警告 (琥珀金/日落橙/深褐)
  delegation: ['#ffcc00', '#ff9500', '#ff3b30', '#1c1c1e'] as [string, string, string, string],
};

type FadeDirection = 'none' | 'fade-right' | 'fade-left' | 'fade-bottom';

export function MeshGradient({ 
  colors = MESH_PALETTES.incident, 
  fade = 'fade-right',
  className,
  style 
}: { 
  colors?: [string, string, string, string],
  fade?: FadeDirection,
  className?: string,
  style?: React.CSSProperties
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mounted, setMounted] = useState(false);
  const uniqueId = useId().replace(/:/g, ''); // Generate unique ID for multiple instances

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || !canvasRef.current) return;
    
    const gradient = new Gradient();
    gradient.initGradient('#mesh-canvas-' + uniqueId);

    return () => {
      if (gradient.pause) gradient.pause();
    };
  }, [mounted, uniqueId]);

  if (!mounted) return null;

  // 根据方向生成 CSS Mask (遮罩)，实现参考图 2 中“局部渐隐”的极高质感
  const getMaskStyle = () => {
    switch (fade) {
      case 'fade-right':
        return {
          maskImage: 'linear-gradient(to right, transparent 0%, transparent 30%, black 80%, black 100%)',
          WebkitMaskImage: 'linear-gradient(to right, transparent 0%, transparent 30%, black 80%, black 100%)',
        };
      case 'fade-left':
        return {
          maskImage: 'linear-gradient(to left, transparent 0%, transparent 30%, black 80%, black 100%)',
          WebkitMaskImage: 'linear-gradient(to left, transparent 0%, transparent 30%, black 80%, black 100%)',
        };
      case 'fade-bottom':
        return {
          maskImage: 'linear-gradient(to bottom, transparent 0%, black 50%, black 100%)',
          WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, black 50%, black 100%)',
        };
      default:
        return {};
    }
  };

  return (
    <div 
      className="mesh-gradient-wrapper"
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 0,
        pointerEvents: 'none',
        ...getMaskStyle()
      }}
    >
      <canvas 
        id={'mesh-canvas-' + uniqueId}
        ref={canvasRef} 
        className={className}
        style={{
          ...style,
          width: '100%',
          height: '100%',
          '--gradient-color-1': colors[0],
          '--gradient-color-2': colors[1],
          '--gradient-color-3': colors[2],
          '--gradient-color-4': colors[3],
        } as React.CSSProperties} 
      />
      {/* 物理透镜叠加，增加高级柔和感 */}
      <div 
        style={{
          position: 'absolute',
          inset: 0,
          backdropFilter: 'blur(30px) saturate(1.2)',
          WebkitBackdropFilter: 'blur(30px) saturate(1.2)',
          backgroundColor: 'rgba(15, 15, 18, 0.2)' // 极暗底色
        }}
      />
    </div>
  );
}
