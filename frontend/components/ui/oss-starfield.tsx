'use client';

import { memo } from 'react';
import { Canvas } from '@react-three/fiber';
import { Stars } from '@react-three/drei';

interface OSSStarfieldProps {
  theme: 'light' | 'dark';
  panelLevel: number;
}

function StarLayers() {
  return (
    <>
      <group rotation={[Math.PI / 7, 0, Math.PI / 12]}>
        <Stars radius={110} depth={42} count={3200} factor={2.4} saturation={0} fade speed={0.16} />
      </group>
      <group rotation={[-Math.PI / 8, Math.PI / 9, 0]}>
        <Stars radius={76} depth={18} count={900} factor={4.6} saturation={0} fade speed={0.05} />
      </group>
    </>
  );
}

function OSSStarfield({ theme, panelLevel }: OSSStarfieldProps) {
  const opacity = theme === 'light' ? 0.12 : panelLevel === 0 ? 0.38 : 0.18;

  return (
    <div
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: '-12%',
        pointerEvents: 'none',
        opacity,
        filter: theme === 'light' ? 'blur(0.35px)' : 'blur(0.15px)',
        transform: 'scale(1.02)',
      }}
    >
      <Canvas
        dpr={[1, 1.4]}
        gl={{ alpha: true, antialias: false, powerPreference: 'low-power' }}
        camera={{ position: [0, 0, 1], fov: 70, near: 0.1, far: 300 }}
      >
        <StarLayers />
      </Canvas>
    </div>
  );
}

export default memo(OSSStarfield);
