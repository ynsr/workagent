import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"
import { componentTagger } from "./componentTagger"

// https://vite.dev/config/
export default defineConfig({
  plugins: [componentTagger(), react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      // Contract: changeOrigin must stay false so the browser's Origin/Host
      // reaches the server and the same-origin check passes.
      "/api": {
        target: "http://127.0.0.1:3344",
        changeOrigin: false,
      },
    },
  },
})
