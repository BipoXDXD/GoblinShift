import { resolve } from "node:path";

import { build, type Plugin } from "vite";

/**
 * Compila o service worker (ADR 062) num `sw.js` sozinho, na raiz do build e sem hash: o
 * navegador o busca sempre pelo mesmo nome, e o escopo dele é o caminho de onde vem. Como entrada
 * do build da página, o código que ele divide com ela (a caixa de entrada) iria para um chunk
 * importado, e um service worker clássico não aceita `import`; o registro `type: "module"` ainda
 * não vale em todo navegador. Por isso um build próprio, em IIFE, depois do da página.
 */
export function serviceWorker(entry: string): Plugin {
  let outDir = "dist";
  return {
    name: "goblinshift-service-worker",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir;
    },
    async closeBundle() {
      await build({
        configFile: false,
        logLevel: "warn",
        publicDir: false,
        build: {
          outDir,
          emptyOutDir: false,
          sourcemap: false,
          minify: true,
          lib: {
            entry: resolve(entry),
            formats: ["iife"],
            name: "goblinShiftServiceWorker",
            fileName: () => "sw.js",
          },
        },
      });
    },
  };
}
