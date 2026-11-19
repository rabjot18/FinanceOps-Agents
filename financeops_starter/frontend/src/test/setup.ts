import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup, configure } from '@testing-library/react';

// The first render in a file (React Flow included) can take over a second when
// every test file runs in parallel; give findBy* queries room before failing.
configure({ asyncUtilTimeout: 4000 });

// React Flow measures nodes with ResizeObserver and reads the viewport
// transform with DOMMatrixReadOnly; jsdom has neither (see the React Flow
// testing guide). Sizes come from inline styles, or 1px, so nodes count as
// measured and render.
class ResizeObserverStub {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    // Asynchronous, like the real one: React Flow registers its DOM node first.
    setTimeout(() => this.callback([{ target } as ResizeObserverEntry], this as unknown as ResizeObserver), 0);
  }
  unobserve() {}
  disconnect() {}
}

class DOMMatrixReadOnlyStub {
  m22: number;
  constructor(transform?: string) {
    const scale = transform?.match(/scale\(([1-9.]+)\)/)?.[1];
    this.m22 = scale !== undefined ? Number(scale) : 1;
  }
}

// Always replace both: jsdom's own versions (if any) never report a size or parse a transform.
for (const scope of [globalThis, window] as unknown as Record<string, unknown>[]) {
  scope.ResizeObserver = ResizeObserverStub;
  scope.DOMMatrixReadOnly = DOMMatrixReadOnlyStub;
}
Object.defineProperties(HTMLElement.prototype, {
  offsetHeight: {
    configurable: true,
    get(this: HTMLElement) {
      return parseFloat(this.style.height) || 1;
    },
  },
  offsetWidth: {
    configurable: true,
    get(this: HTMLElement) {
      return parseFloat(this.style.width) || 1;
    },
  },
});
if (!('getBBox' in SVGElement.prototype)) {
  (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () =>
    ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  window.location.hash = '';
});
