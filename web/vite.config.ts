import { resolve } from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { configDefaults, defineConfig } from "vitest/config";

import { FONT_PRELOAD } from "./scripts/check-budget";
import { preloadFonts } from "./scripts/vite-preload-fonts";

/**
 * Só para o build do E2E com aviso (npm run build:notice-e2e): troca o `notice.json` do
 * repositório pelo arquivo indicado, sem editar o fonte. Sem a variável, vale o do repositório.
 */
const noticeFile = process.env["GS_NOTICE_FILE"];

export default defineConfig({
  plugins: [react(), tailwindcss(), preloadFonts(FONT_PRELOAD)],
  resolve: {
    alias:
      noticeFile === undefined || noticeFile === ""
        ? []
        : [{ find: /^\.\/notice\.json$/, replacement: resolve(noticeFile) }],
  },
  // Os textos legais vêm de docs/legal/ (fonte única, fora de web/); o servidor de dev só serve o
  // que está na allowlist.
  server: { fs: { allow: [".", "../docs/legal"] } },
  build: {
    // Sem source map no bundle publicado: o fonte embutido vazaria comentários (ETS item 73).
    sourcemap: false,
    // O chunk do 3D da abertura (three.js, ~615 KB crus) passa do aviso padrão de 500 KB de
    // propósito; quem manda no peso é o check-budget, com os tetos do ADR 027 e 030.
    chunkSizeWarningLimit: 650,
    rolldownOptions: {
      output: {
        // O framework muda pouco: em chunks próprios, o cache do navegador sobrevive aos deploys da
        // interface, e nenhum chunk chega perto do teto de tamanho cru do orçamento.
        advancedChunks: {
          groups: [
            { name: "react", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: "router", test: /node_modules[\\/]react-router[\\/]/ },
            // O z.config({ jitless }) mora no chunk do próprio Zod: ele roda antes de qualquer
            // chunk que crie schema, seja qual for a divisão. Fora dele, um schema num chunk
            // compartilhado nascia antes da configuração e o teste de eval violava a CSP.
            { name: "zod", test: /node_modules[\\/]zod[\\/]|src[\\/]zodJitless\.ts$/ },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // As specs do Playwright (e2e/) rodam com o próprio runner, num navegador de verdade.
    exclude: [...configDefaults.exclude, "e2e/**"],
    // Sem isso o Vitest troca todo CSS por módulo vazio, inclusive o `?raw` que o teste de
    // contraste e o de licenças leem; os componentes não importam CSS.
    css: { include: [/(tokens|fonts)\.css/] },
    restoreMocks: true,
    unstubGlobals: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/main.tsx", "src/api/schema.d.ts"],
    },
  },
});
