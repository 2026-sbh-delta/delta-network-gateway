import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";
import { prepareBaseline } from "./scripts/production-ui.mjs";

function productionUI() {
  return {
    name: "preserve-production-ui",
    configureServer(server) {
      const baseline = prepareBaseline();
      server.httpServer?.once("close", () =>
        fs.rmSync(baseline.directory, { recursive: true, force: true }),
      );
      server.middlewares.use(async (request, response, next) => {
        const url = new URL(request.url || "/", "http://localhost");
        if (url.pathname === "/") {
          // These compiled modules are served as exact baseline bytes below.
          // Let Vite transform only the new source entry, without warming them.
          const modules = [];
          const sourceHTML = baseline.html.replace(
            /<script\b[^>]*\bsrc="\/assets\/[^\"]+"[^>]*><\/script>/g,
            (tag) => {
              const marker = `<!-- production-module-${modules.length} -->`;
              modules.push({ marker, tag });
              return marker;
            },
          );
          let html = await server.transformIndexHtml(
            "/",
            sourceHTML.replace(
              "</body>",
              '<script type="module" src="/src/production-pipeline-entry.tsx"></script>\n</body>',
            ),
          );
          for (const { marker, tag } of modules)
            html = html.replace(marker, tag);
          response.setHeader("Content-Type", "text/html; charset=utf-8");
          response.end(html);
        } else if (
          /^\/assets\/[A-Za-z0-9_.-]+\.(?:js|css)$/.test(url.pathname)
        ) {
          const file = path.join(baseline.directory, url.pathname);
          if (!fs.existsSync(file)) return next();
          response.setHeader(
            "Content-Type",
            url.pathname.endsWith(".js") ? "text/javascript" : "text/css",
          );
          response.end(fs.readFileSync(file));
        } else next();
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "production-ui" ? [productionUI()] : [])],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8088", changeOrigin: false } },
  },
}));
