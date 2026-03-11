import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// In Docker, the backend is reachable via service name "backend"
// Locally, it's on localhost:8000
const apiTarget = process.env.VITE_API_URL || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
