import { afterAll, afterEach, beforeAll } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import initSqlJs from 'sql.js';
import { server } from '@/mocks/server';

// Provide the SQLite engine for db-import tests. The app loads it in the
// browser via <script src="/sql-wasm.js"> (a public asset, see lib/db-import.ts);
// vitest runs in Node where there is no script tag, so the real module is
// injected here. This file only ever runs under vitest — never part of the
// Next.js browser bundle — so the CJS glue's Node-only requires are safe.
(globalThis as unknown as { initSqlJs?: typeof initSqlJs }).initSqlJs = initSqlJs;

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
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
