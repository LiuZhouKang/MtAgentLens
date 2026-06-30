'use client';
import { cn } from '@/lib/utils';
import { AnimatePresence, Transition, motion } from 'motion/react';
import {
  Children,
  cloneElement,
  ReactElement,
  useEffect,
  useState,
  useId,
} from 'react';

export type AnimatedBackgroundProps = {
  children:
    | ReactElement<{ 'data-id': string }>[]
    | ReactElement<{ 'data-id': string }>;
  value?: string | null;
  defaultValue?: string;
  onValueChange?: (newActiveId: string | null) => void;
  className?: string;
  transition?: Transition;
  enableHover?: boolean;
};

export function AnimatedBackground({
  children,
  value,
  defaultValue,
  onValueChange,
  className,
  transition,
  enableHover = false,
}: AnimatedBackgroundProps) {
  const [uncontrolledActiveId, setUncontrolledActiveId] = useState<string | null>(defaultValue ?? null);
  const uniqueId = useId();
  const isControlled = value !== undefined;
  const activeId = isControlled ? value : uncontrolledActiveId;

  const handleSetActiveId = (id: string | null) => {
    if (!isControlled) {
      setUncontrolledActiveId(id);
    }

    if (onValueChange) {
      onValueChange(id);
    }
  };

  useEffect(() => {
    if (!isControlled && defaultValue !== undefined) {
      setUncontrolledActiveId(defaultValue);
    }
  }, [defaultValue, isControlled]);

  return Children.map(children, (child: any, index) => {
    const id = child.props['data-id'];
    const isActive = activeId === id;

    const interactionProps = enableHover
      ? {
          onMouseEnter: () => handleSetActiveId(id),
          onMouseLeave: () => handleSetActiveId(null),
        }
      : {
          onClick: () => handleSetActiveId(id),
        };

    return cloneElement(
      child,
      {
        key: index,
        className: cn('relative inline-flex', child.props.className),
        'data-checked': isActive ? 'true' : 'false',
        'data-state': isActive ? 'active' : 'inactive',
        'aria-pressed': !enableHover ? isActive : undefined,
        ...interactionProps,
      },
      <>
        <AnimatePresence initial={false}>
          {activeId === id && (
            <motion.div
              layoutId={`background-${uniqueId}`}
              className={cn('absolute inset-0', className)}
              transition={transition}
              initial={{ opacity: defaultValue ? 1 : 0 }}
              animate={{
                opacity: 1,
              }}
              exit={{
                opacity: 0,
              }}
            />
          )}
        </AnimatePresence>
        <div className='z-10'>{child.props.children}</div>
      </>
    );
  });
}
