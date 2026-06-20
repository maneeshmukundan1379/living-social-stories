import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backendPort = process.env.BACKEND_PORT || "8001";
const frontendPort = parseInt(process.env.FRONTEND_PORT || "5174", 10);
const proxyTarget = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: frontendPort,
    strictPort: false,
    host: "127.0.0.1",
    proxy: {
      "/api": { target: proxyTarget, changeOrigin: true },
      "/health": { target: proxyTarget, changeOrigin: true },
      "/media": { target: proxyTarget, changeOrigin: true },
    },
  },
});
