import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

import { installFakeMatchMedia } from "./fakeMatchMedia";

// As telas carregam por rota (lazy): no primeiro teste de cada arquivo, o Vitest ainda transforma
// o módulo da tela, o que passa do 1 s padrão do findBy no CI. 5 s só pesa quando o teste já ia
// falhar.
configure({ asyncUtilTimeout: 5_000 });

// O jsdom não implementa o `<dialog>` modal. O dublê só abre e fecha, com o evento `close`; o foco
// preso e o Esc de verdade são do navegador e ficam com o Playwright (e2e/specs/access.spec.ts).
HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement): void {
  this.setAttribute("open", "");
};
HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement): void {
  if (this.hasAttribute("open")) {
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  }
};

beforeEach(() => {
  // O cabeçalho do app tem o seletor de tema, que consulta o sistema; o padrão é tema claro.
  installFakeMatchMedia(false);
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset["theme"];
});
