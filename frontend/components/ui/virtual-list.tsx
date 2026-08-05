'use client';
import { useRef, type ReactNode } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

/**
 * Generic virtualized list built on @tanstack/react-virtual.
 *
 * Only the visible rows (+ overscan) are mounted in the DOM, so thousands of
 * timeline steps / alerts / evidence rows never create thousands of DOM nodes.
 */
type Props<T> = {
  items: T[];
  estimateSize?: number;
  overscan?: number;
  /** True when rows have variable heights — enables per-row measurement. */
  dynamic?: boolean;
  renderRow: (item: T, index: number) => ReactNode;
  className?: string;
  emptyLabel?: string;
};

export default function VirtualList<T>({
  items,
  estimateSize = 40,
  overscan = 8,
  dynamic = false,
  renderRow,
  className,
  emptyLabel = '暂无数据',
}: Props<T>) {
  const ref = useRef<HTMLDivElement>(null);
  // TanStack Virtual's `useVirtualizer` returns functions that can't be memoized
  // (React Compiler rule). This project doesn't use React Compiler, so the
  // warning is benign — the API itself is the recommended usage.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => ref.current,
    estimateSize: () => estimateSize,
    overscan,
    // Render a first batch before the ResizeObserver measures the container —
    // prevents a blank flash (and works where measurement is unavailable).
    initialRect: { width: 800, height: 400 },
    ...(dynamic ? { measureElement: (el: Element) => el.getBoundingClientRect().height } : {}),
  });

  if (items.length === 0) {
    return (
      <div
        className={className}
        style={{ padding: '12px', textAlign: 'center', color: 'rgba(255,255,255,0.35)' }}
      >
        {emptyLabel}
      </div>
    );
  }

  return (
    <div
      ref={ref}
      className={className}
      style={{ overflow: 'auto', height: '100%' }}
    >
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
        {virtualizer.getVirtualItems().map((vi) => (
          <div
            key={vi.key}
            ref={dynamic ? virtualizer.measureElement : undefined}
            data-index={dynamic ? vi.index : undefined}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: vi.size,
              transform: `translateY(${vi.start}px)`,
            }}
          >
            {renderRow(items[vi.index], vi.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
