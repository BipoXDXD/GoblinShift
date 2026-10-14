import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

import { installFakeMatchMedia } from "./fakeMatchMedia";

beforeEach(() => {
  // O cabeçalho do app tem o seletor de tema, que consulta o sistema; o padrão é tema claro.
  installFakeMatchMedia(false);
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset["theme"];
});
