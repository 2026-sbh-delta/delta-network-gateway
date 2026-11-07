import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../.runtime/pipeline-extension",
    emptyOutDir: true,
    lib: {
      entry: "src/production-pipeline-entry.tsx",
      formats: ["es"],
      fileName: "pipeline-extension",
      cssFileName: "pipeline-extension",
    },
    minify: true,
  },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
});
