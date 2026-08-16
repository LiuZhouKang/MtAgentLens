'use client';
import { cn } from '@/lib/utils';
import { animate, motion, useMotionValue, useTransform } from 'motion/react';
import { useEffect } from 'react';

export type AnimatedNumberProps = {
  value: number;
  className?: string;
};

/**
 * 顶级数字动画：从当前值平滑滚动到目标值（首次挂载呈现「0 → 目标」的滚动
 * 入场，切换告警/工作台时旧值 → 新值平滑过渡），并配合轻微淡入。
 * 千分位格式化（toLocaleString）保证大数字可读。用 useMotionValue + animate
 * 驱动（不触发 React 逐帧 setState），动画流畅且开销极低。
 */
export function AnimatedNumber({ value, className }: AnimatedNumberProps) {
  const motionValue = useMotionValue(0);
  const display = useTransform(motionValue, (current) => Math.round(current).toLocaleString());

  useEffect(() => {
    const controls = animate(motionValue, value, {
      duration: 0.8,
      ease: [0.22, 1, 0.36, 1],
    });
    return () => controls.stop();
  }, [motionValue, value]);

  return (
    <motion.span
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35, delay: 0.1 }}
      className={cn('tabular-nums', className)}
    >
      {display}
    </motion.span>
  );
}
