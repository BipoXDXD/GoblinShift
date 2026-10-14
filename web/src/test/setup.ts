import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

import { installFakeMatchMedia } from "./fakeMatchMedia";

// As telas carregam por rota (lazy): no primeiro teste de cada arquivo, o Vitest ainda transforma
// o módulo da tela, o que passa do 1 s padrão do findBy no CI. 5 s só pesa quando o teste já ia
// falhar.
configure({ asyncUtilTimeout: 5_000 });

beforeEach(() => {
  // O cabeçalho do app tem o seletor de tema, que consulta o sistema; o padrão é tema claro.
  installFakeMatchMedia(false);
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset["theme"];
});
