'use client';

import { useCallback, useMemo, type ReactNode } from 'react';
import {
  motion,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useSpring,
  type HTMLMotionProps,
} from 'framer-motion';

type RevealProps = HTMLMotionProps<'div'> & {
  delay?: number;
  distance?: number;
  once?: boolean;
  blur?: boolean;
  disabled?: boolean;
  mode?: 'view' | 'mount';
};

type StaggerGroupProps = HTMLMotionProps<'div'> & {
  delay?: number;
  stagger?: number;
};

type MagneticButtonProps = HTMLMotionProps<'button'> & {
  intensity?: number;
  motionDisabled?: boolean;
};

type TiltPanelProps = HTMLMotionProps<'div'> & {
  intensity?: number;
  glare?: boolean;
  disableMotion?: boolean;
};

export function Reveal({
  children,
  delay = 0,
  distance = 18,
  once = true,
  blur = true,
  disabled = false,
  mode = 'view',
  style,
  transition,
  ...props
}: RevealProps) {
  const reduceMotion = useReducedMotion();

  if (disabled) {
    return (
      <motion.div initial={false} style={style} {...props}>
        {children as ReactNode}
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={
        reduceMotion
          ? { opacity: 0 }
          : { opacity: 0, y: distance, filter: blur ? 'blur(12px)' : 'blur(0px)' }
      }
      animate={
        mode === 'mount'
          ? reduceMotion
            ? { opacity: 1 }
            : { opacity: 1, y: 0, filter: 'blur(0px)' }
          : undefined
      }
      whileInView={
        mode === 'view'
          ? reduceMotion
            ? { opacity: 1 }
            : { opacity: 1, y: 0, filter: 'blur(0px)' }
          : undefined
      }
      viewport={mode === 'view' ? { once, margin: '-12% 0px' } : undefined}
      transition={{
        delay,
        duration: reduceMotion ? 0.18 : 0.66,
        ease: [0.16, 1, 0.3, 1],
        ...transition,
      }}
      style={style}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function StaggerGroup({
  children,
  delay = 0,
  stagger = 0.08,
  transition,
  ...props
}: StaggerGroupProps) {
  const reduceMotion = useReducedMotion();
  const variants = useMemo(
    () => ({
      hidden: {},
      visible: {
        transition: reduceMotion
          ? { delayChildren: delay }
          : { delayChildren: delay, staggerChildren: stagger },
      },
    }),
    [delay, reduceMotion, stagger],
  );

  return (
    <motion.div
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: '-12% 0px' }}
      variants={variants}
      transition={transition}
      {...props}
    >
      {children}
    </motion.div>
  );
}

export function MagneticButton({
  children,
  intensity = 14,
  motionDisabled = false,
  onPointerMove,
  onPointerLeave,
  whileTap,
  style,
  transition,
  ...props
}: MagneticButtonProps) {
  const reduceMotion = useReducedMotion();
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const springX = useSpring(x, { stiffness: 240, damping: 18, mass: 0.25 });
  const springY = useSpring(y, { stiffness: 240, damping: 18, mass: 0.25 });

  const handlePointerMove = useCallback<NonNullable<MagneticButtonProps['onPointerMove']>>(
    (event) => {
      onPointerMove?.(event);
      if (reduceMotion) return;
      const rect = event.currentTarget.getBoundingClientRect();
      const offsetX = event.clientX - rect.left - rect.width / 2;
      const offsetY = event.clientY - rect.top - rect.height / 2;
      x.set((offsetX / rect.width) * intensity);
      y.set((offsetY / rect.height) * intensity);
    },
    [intensity, onPointerMove, reduceMotion, x, y],
  );

  const handlePointerLeave = useCallback<NonNullable<MagneticButtonProps['onPointerLeave']>>(
    (event) => {
      onPointerLeave?.(event);
      x.set(0);
      y.set(0);
    },
    [onPointerLeave, x, y],
  );

  if (motionDisabled) {
    return (
      <motion.button
        initial={false}
        style={style}
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        {...props}
      >
        {children as ReactNode}
      </motion.button>
    );
  }

  return (
    <motion.button
      whileTap={whileTap ?? (reduceMotion ? undefined : { scale: 0.985 })}
      transition={
        transition ?? {
          duration: 0.22,
          ease: [0.22, 1, 0.36, 1],
        }
      }
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      style={{
        ...style,
        x: reduceMotion ? 0 : springX,
        y: reduceMotion ? 0 : springY,
        willChange: 'transform',
      }}
      {...props}
    >
      {children}
    </motion.button>
  );
}

export function TiltPanel({
  children,
  intensity = 7,
  glare = true,
  disableMotion = false,
  onPointerMove,
  onPointerLeave,
  style,
  ...props
}: TiltPanelProps) {
  const reduceMotion = useReducedMotion();
  const rotateX = useMotionValue(0);
  const rotateY = useMotionValue(0);
  const glareX = useMotionValue(50);
  const glareY = useMotionValue(50);
  const springRotateX = useSpring(rotateX, { stiffness: 210, damping: 22, mass: 0.34 });
  const springRotateY = useSpring(rotateY, { stiffness: 210, damping: 22, mass: 0.34 });
  const glareBackground = useMotionTemplate`radial-gradient(circle at ${glareX}% ${glareY}%, rgba(255,255,255,0.18), transparent 36%)`;

  const handlePointerMove = useCallback<NonNullable<TiltPanelProps['onPointerMove']>>(
    (event) => {
      onPointerMove?.(event);
      if (reduceMotion) return;
      const rect = event.currentTarget.getBoundingClientRect();
      const offsetX = event.clientX - rect.left;
      const offsetY = event.clientY - rect.top;
      const ratioX = offsetX / rect.width;
      const ratioY = offsetY / rect.height;
      rotateY.set((ratioX - 0.5) * intensity * 2);
      rotateX.set((0.5 - ratioY) * intensity * 2);
      glareX.set(ratioX * 100);
      glareY.set(ratioY * 100);
    },
    [glareX, glareY, intensity, onPointerMove, reduceMotion, rotateX, rotateY],
  );

  const handlePointerLeave = useCallback<NonNullable<TiltPanelProps['onPointerLeave']>>(
    (event) => {
      onPointerLeave?.(event);
      rotateX.set(0);
      rotateY.set(0);
      glareX.set(50);
      glareY.set(50);
    },
    [glareX, glareY, onPointerLeave, rotateX, rotateY],
  );

  if (disableMotion) {
    return (
      <motion.div
        initial={false}
        style={style}
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        {...props}
      >
        {children as ReactNode}
      </motion.div>
    );
  }

  return (
    <motion.div
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      whileHover={reduceMotion ? undefined : { y: -4, scale: 1.01 }}
      style={{
        ...style,
        position: 'relative',
        transformStyle: 'preserve-3d',
        willChange: 'transform',
        rotateX: reduceMotion ? 0 : springRotateX,
        rotateY: reduceMotion ? 0 : springRotateY,
      }}
      {...props}
    >
      {glare ? (
        <motion.div
          aria-hidden="true"
          style={{
            position: 'absolute',
            inset: 0,
            borderRadius: 'inherit',
            pointerEvents: 'none',
            background: glareBackground,
            opacity: reduceMotion ? 0 : 0.7,
            mixBlendMode: 'screen',
          }}
        />
      ) : null}
      <div style={{ position: 'relative', zIndex: 1 }}>{children as ReactNode}</div>
    </motion.div>
  );
}
