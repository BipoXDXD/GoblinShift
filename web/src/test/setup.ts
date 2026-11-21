import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

import { startConsoleGuard, stopConsoleGuard, writeConsoleReport } from "./consoleGuard";
import { installFakeMatchMedia } from "./fakeMatchMedia";

// As telas carregam por rota (lazy): no primeiro teste de cada arquivo, o Vitest ainda transforma
// o módulo da tela, o que passa do 1 s padrão do findBy no CI. 5 s só pesa quando o teste já ia
// falhar. O `testTimeout` do vite.config.ts fica acima disto, e o setup.test.ts confere.
configure({ asyncUtilTimeout: 5_000 });

// O jsdom não implementa o `<dialog>` modal. O dublê abre e fecha, com o evento `close`, e devolve o
// foco a quem abriu, como o navegador; o foco preso e o Esc de verdade são do navegador e ficam com
// o Playwright (e2e/specs/access.spec.ts).
const openers = new WeakMap<HTMLDialogElement, Element | null>();
HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement): void {
  openers.set(this, document.activeElement);
  this.setAttribute("open", "");
};
HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement): void {
  if (this.hasAttribute("open")) {
    this.removeAttribute("open");
    const opener = openers.get(this);
    if (opener instanceof HTMLElement && opener.isConnected) {
      opener.focus();
    }
    this.dispatchEvent(new Event("close"));
  }
};

// Modo relatório: as violações do guard de console (src/test/consoleGuard.ts) saem no console
// original com o arquivo e o nome do teste, mas não reprovam o teste.
function reportConsoleViolations(violations: readonly string[]): void {
  if (violations.length === 0) {
    return;
  }
  const { testPath, currentTestName } = expect.getState();
  writeConsoleReport([
    `[console-guard] ${testPath ?? "?"} > ${currentTestName ?? "?"}`,
    ...violations,
  ]);
}

beforeEach(() => {
  startConsoleGuard();
  // O cabeçalho do app tem o seletor de tema, que consulta o sistema; o padrão é tema claro.
  installFakeMatchMedia(false);
  // O jsdom não tem canvas: sem isto ele loga "Not implemented" e a home tentaria a cena 3D. Com
  // `null`, o app se comporta como num navegador sem WebGL e fica na imagem estática.
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset["theme"];
  // Por último, depois do cleanup(): o aviso que um componente dá ao desmontar conta para o teste.
  reportConsoleViolations(stopConsoleGuard());
});
