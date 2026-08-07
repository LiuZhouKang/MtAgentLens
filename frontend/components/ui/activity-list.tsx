'use client';

import { memo, useCallback, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

interface Activity {
  id: string;
  title: string;
  description: string;
  timestamp: string;
  type: string;
}

interface ActivityItemProps {
  activity: Activity;
}

/** A single activity row. Memoized for virtualized list performance. */
const ActivityItem = memo(function ActivityItem({ activity }: ActivityItemProps) {
  return (
    <div className="border-b border-gray-200 p-3">
      <h3 className="font-medium text-gray-900">{activity.title}</h3>
      <p className="text-sm text-gray-500">{activity.description}</p>
      <span className="text-xs text-gray-400">{activity.timestamp}</span>
    </div>
  );
});

interface ActivityListProps {
  activities: Activity[];
}

/** Virtualized activity list with keyboard navigation (ArrowUp/Down, Home/End). */
export function ActivityList({ activities }: ActivityListProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState(-1);

  // TanStack Virtual intentionally returns imperative helpers that React Compiler
  // cannot safely memoize today; the hook itself is still the supported API.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: activities.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 80,
    overscan: 5,
  });

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const lastIndex = activities.length - 1;
      if (lastIndex < 0) return;

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setActiveIndex((prev) => {
            const next = prev < lastIndex ? prev + 1 : 0;
            virtualizer.scrollToIndex(next, { align: 'auto' });
            return next;
          });
          break;
        case 'ArrowUp':
          e.preventDefault();
          setActiveIndex((prev) => {
            const next = prev > 0 ? prev - 1 : lastIndex;
            virtualizer.scrollToIndex(next, { align: 'auto' });
            return next;
          });
          break;
        case 'Home':
          e.preventDefault();
          setActiveIndex(0);
          virtualizer.scrollToIndex(0, { align: 'start' });
          break;
        case 'End':
          e.preventDefault();
          setActiveIndex(lastIndex);
          virtualizer.scrollToIndex(lastIndex, { align: 'end' });
          break;
      }
    },
    [activities.length, virtualizer],
  );

  if (activities.length === 0) {
    return (
      <p className="p-4 text-gray-500" role="status">
        No activities yet.
      </p>
    );
  }

  const activeId =
    activeIndex >= 0 && activeIndex < activities.length
      ? `activity-${activities[activeIndex].id}`
      : undefined;

  return (
    <div
      ref={parentRef}
      role="listbox"
      aria-label="Activity history"
      aria-activedescendant={activeId}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className="activity-list max-h-96 overflow-y-auto rounded-lg border border-gray-200 focus:ring-2 focus:ring-blue-500 focus:outline-none"
    >
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const activity = activities[virtualRow.index];
          const isActive = virtualRow.index === activeIndex;
          return (
            <div
              key={virtualRow.key}
              id={`activity-${activity.id}`}
              role="option"
              aria-selected={isActive}
              aria-posinset={virtualRow.index + 1}
              aria-setsize={activities.length}
              data-index={virtualRow.index}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
              className={isActive ? 'bg-blue-50' : undefined}
            >
              <ActivityItem activity={activity} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
