import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // Sem source map no bundle publicado: o fonte embutido vazaria comentários (ETS item 73).
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
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
