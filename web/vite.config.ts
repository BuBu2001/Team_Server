import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Сборка кладётся в web/dist и раздаётся FastAPI (app_platform) как основной сайт.
// В dev-режиме (npm run dev) запросы к API проксируются на локальный сервер.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // @excalidraw/excalidraw подключается через CJS-обёртку main.js, где выбор
  // бандла зависит от process.env.IS_PREACT. В браузере process не существует,
  // поэтому подставляем значение явно — иначе вкладка падает с ReferenceError.
  define: {
    "process.env.IS_PREACT": JSON.stringify("false"),
  },
  optimizeDeps: {
    esbuildOptions: {
      define: {
        "process.env.IS_PREACT": JSON.stringify("false"),
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/site": "http://127.0.0.1:8000",
    },
  },
});
