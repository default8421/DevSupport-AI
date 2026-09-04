/** @author: liuqinhe */
import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // 部署在反代的子路径下时用 VITE_BASE 指定前缀（如 /devsupport/），本地开发保持 /
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          // 只拆稳定、跨页共享的 vendor。antd / X 不强制合并——
          // 否则 Table/Drawer 这类内部页组件会被打进人人下载的公共包，
          // 客户侧首屏就会被工作台的体积拖过 453 kB 门槛。
          if (id.includes("react-markdown") || id.includes("remark-gfm")) return "markdown";
          if (
            id.includes("node_modules/react-dom") ||
            id.includes("node_modules/react/") ||
            id.includes("node_modules/scheduler") ||
            id.includes("node_modules/react-router")
          ) {
            return "react-vendor";
          }
          return undefined;
        },
      },
    },
  },
});
