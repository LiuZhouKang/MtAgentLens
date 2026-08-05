// @vitest-environment jsdom
import { beforeAll, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import VirtualList from '@/components/ui/virtual-list';

beforeAll(() => {
  // jsdom doesn't do layout — the virtualizer measures the scroll element via
  // offsetWidth/offsetHeight (virtual-core getRect) and sees 0. Stub fixed
  // sizes so visible rows are computed. jsdom defines these on
  // HTMLElement.prototype, so patch there (Element.prototype is shadowed).
  for (const prop of ['offsetHeight', 'offsetWidth', 'clientHeight', 'clientWidth']) {
    Object.defineProperty(HTMLElement.prototype, prop, {
      configurable: true,
      get() {
        return prop.endsWith('Height') ? 300 : 800;
      },
    });
  }
});

// VirtualList needs a bounded-height parent so the virtualizer computes visible rows.
function InBox({ children }: { children: React.ReactNode }) {
  return <div style={{ height: 300 }}>{children}</div>;
}

describe('VirtualList', () => {
  it('renders rows for provided items', () => {
    render(
      <InBox>
        <VirtualList items={['a', 'b', 'c']} renderRow={(item) => <span>{item}</span>} />
      </InBox>,
    );
    expect(screen.getByText('a')).toBeTruthy();
    expect(screen.getByText('c')).toBeTruthy();
  });

  it('shows empty label when no items', () => {
    render(<VirtualList items={[]} emptyLabel="空" renderRow={() => null} />);
    expect(screen.getByText('空')).toBeTruthy();
  });

  it('virtualizes a large list (renders a subset of DOM rows)', () => {
    const items = Array.from({ length: 5000 }, (_, i) => `row-${i}`);
    render(
      <InBox>
        <VirtualList items={items} estimateSize={30} renderRow={(item) => <span>{item}</span>} />
      </InBox>,
    );
    // 虚拟化：只渲染可见行 + overscan，绝不可能 5000 行全挂载
    const mounted = document.querySelectorAll('[style*="translateY"]').length;
    expect(mounted).toBeGreaterThan(0);
    expect(mounted).toBeLessThan(200);
  });
});
