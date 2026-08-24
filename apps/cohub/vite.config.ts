import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: "frontend",
  build: {
    outDir: "../cohub/static",
    emptyOutDir: true,
    assetsDir: "assets",
  },
});
