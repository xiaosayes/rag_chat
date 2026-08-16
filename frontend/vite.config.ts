import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import * as path from "path";
import postcsspxtoviewport from "postcss-px-to-viewport";

// 竖屏一体机 1080 宽设计稿 → vmin（竖屏下 vmin=屏宽比），横屏/大屏同体系缩放（参考工程既有适配机制）
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname);
  const apiUrl = env.VITE_API_URL || "http://127.0.0.1:7861";
  return {
    base: "./",
    plugins: [vue()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    css: {
      postcss: {
        plugins: [
          postcsspxtoviewport({
            unitToConvert: "px",
            viewportWidth: 1080,   // UI 设计稿宽度（竖屏一体机）
            unitPrecision: 6,
            propList: ["*"],
            viewportUnit: "vmin",
            fontViewportUnit: "vmin",
            minPixelValue: 1,
            mediaQuery: true,
            exclude: [/node_modules/],
          }),
        ],
      },
    },
    server: {
      host: true,
      proxy: {
        "/api": { target: apiUrl, changeOrigin: true },
        "/ws": { target: apiUrl.replace(/^http/, "ws"), ws: true, changeOrigin: true },
      },
    },
    build: { target: "es2020", chunkSizeWarningLimit: 2048 },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["tests/setup.ts"],
    },
  };
});
