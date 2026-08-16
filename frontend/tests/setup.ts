/** vitest 全局 setup（web-014）。 */
import { vi } from "vitest";

// jsdom 无 matchMedia / AudioContext / WebGL —— 组件测试统一打桩
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (q: string) => ({
    matches: false, media: q,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(),
    onchange: null, dispatchEvent: vi.fn(),
  }),
});
