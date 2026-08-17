import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import * as path from "path";

// web-034：1080×1920 设计坐标系定版 + App.vue 舞台等比缩放（弃 px→vmin，避免双重缩放）
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname);
  const apiUrl = env.VITE_API_URL || "http://127.0.0.1:7861";
  return {
    base: "./",
    plugins: [vue()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
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
