'use client';

import * as React from 'react';
import { useRef } from 'react';
import Link from 'next/link';
import clsx from 'clsx';
import { twMerge } from 'tailwind-merge';
import {
  MotionValue,
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from 'motion/react';

const cn = (...args: any[]) => twMerge(clsx(args));

export interface AnimatedDockProps {
  className?: string;
  items: DockItemData[];
  orientation?: 'horizontal' | 'vertical';
  style?: React.CSSProperties;
  itemStyle?: React.CSSProperties;
  selectedItemStyle?: React.CSSProperties;
  badgeWrapperStyle?: React.CSSProperties;
}

export interface DockItemData {
  link?: string;
  Icon: React.ReactNode;
  target?: string;
  label?: string;
  ariaLabel?: string;
  ariaExpanded?: boolean;
  controls?: string;
  onClick?: () => void;
  selected?: boolean;
  badge?: React.ReactNode;
}

export const AnimatedDock = ({
  className,
  items,
  orientation = 'horizontal',
  style,
  itemStyle,
  selectedItemStyle,
  badgeWrapperStyle,
}: AnimatedDockProps) => {
  const pointer = useMotionValue(Infinity);
  const isVertical = orientation === 'vertical';
  const reduceMotion = useReducedMotion();

  return (
    <motion.div
      onMouseMove={(e) => pointer.set(isVertical ? e.clientY : e.pageX)}
      onMouseLeave={() => pointer.set(Infinity)}
      style={style}
      className={cn(
        'mx-auto flex border border-white/10 bg-white/[0.06] shadow-[0_18px_44px_rgba(0,0,0,0.18)] backdrop-blur-xl',
        isVertical
          ? 'h-auto w-14 flex-col items-center gap-2 rounded-[999px] px-1.5 py-2.5'
          : 'h-16 items-end gap-4 rounded-2xl px-4 pb-3',
        className,
      )}
    >
      {items.map((item, index) => (
        <DockItem
          key={`${item.label ?? 'dock-item'}-${index}`}
          pointer={pointer}
          orientation={orientation}
          selected={item.selected}
          reduceMotion={Boolean(reduceMotion)}
          itemStyle={itemStyle}
          selectedItemStyle={selectedItemStyle}
          badgeWrapperStyle={badgeWrapperStyle}
        >
          {item.link ? (
            <Link
              href={item.link}
              target={item.target}
              aria-label={item.ariaLabel ?? item.label}
              className="flex h-full w-full items-center justify-center"
            >
              {item.Icon}
            </Link>
          ) : (
            <button
              type="button"
              aria-label={item.ariaLabel ?? item.label}
              aria-pressed={item.selected}
              aria-expanded={item.ariaExpanded}
              aria-controls={item.controls}
              onClick={item.onClick}
              className="flex h-full w-full items-center justify-center"
            >
              {item.Icon}
            </button>
          )}
          {item.badge ? (
            <span className="animated-dock__badge" style={badgeWrapperStyle}>
              {item.badge}
            </span>
          ) : null}
        </DockItem>
      ))}
    </motion.div>
  );
};

interface DockItemProps {
  pointer: MotionValue<number>;
  children: React.ReactNode;
  orientation: 'horizontal' | 'vertical';
  selected?: boolean;
  reduceMotion?: boolean;
  itemStyle?: React.CSSProperties;
  selectedItemStyle?: React.CSSProperties;
  badgeWrapperStyle?: React.CSSProperties;
}

export const DockItem = ({
  pointer,
  children,
  orientation,
  selected = false,
  reduceMotion = false,
  itemStyle,
  selectedItemStyle,
}: DockItemProps) => {
  const ref = useRef<HTMLDivElement>(null);
  const isVertical = orientation === 'vertical';

  const distance = useTransform(pointer, (val) => {
    const bounds = ref.current?.getBoundingClientRect() ?? { x: 0, y: 0, width: 0, height: 0 };
    if (isVertical) return val - bounds.y - bounds.height / 2;
    return val - bounds.x - bounds.width / 2;
  });

  const sizeSync = useTransform(distance, [-128, 0, 128], [36, 44, 36]);
  const size = useSpring(sizeSync, {
    mass: 0.16,
    stiffness: 220,
    damping: 23,
  });

  const iconScale = useTransform(size, [36, 44], [1, 1.04]);
  const iconSpring = useSpring(iconScale, {
    mass: 0.14,
    stiffness: 210,
    damping: 22,
  });

  return (
    <motion.div
      ref={ref}
      style={
        reduceMotion
          ? { ...itemStyle, ...(selected ? selectedItemStyle : undefined) }
          : {
              width: size,
              height: size,
              ...itemStyle,
              ...(selected ? selectedItemStyle : undefined),
            }
      }
      whileTap={reduceMotion ? undefined : { scale: 0.985 }}
      data-selected={selected}
      className={cn(
        'animated-dock__item relative flex shrink-0 items-center justify-center rounded-full border border-white/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.1),rgba(255,255,255,0.02))] text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.12),0_12px_24px_rgba(0,0,0,0.16)]',
        selected && 'animated-dock__item--selected',
      )}
    >
      <motion.div
        style={{ scale: iconSpring }}
        className="flex h-full w-full items-center justify-center"
      >
        {children}
      </motion.div>
    </motion.div>
  );
};
