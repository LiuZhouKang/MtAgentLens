import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from '@/mocks/server';

// Mock ResizeObserver for ReactFlow (@xyflow/react)
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// jsdom lacks matchMedia — gsap's ScrollTrigger reads it at module load, so this
// must be in place before any test file imports a component that registers gsap.
globalThis.matchMedia =
  globalThis.matchMedia ??
  function matchMedia() {
    return {
      matches: false,
      media: '',
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  };

globalThis.IntersectionObserver =
  globalThis.IntersectionObserver ??
  class IntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
    root = null;
    rootMargin = '';
    thresholds = [];
  };

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'bypass' });
});

afterEach(() => {
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
