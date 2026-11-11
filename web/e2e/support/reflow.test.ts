import { describe, expect, it } from "vitest";

import { overflowMessage } from "./reflow.ts";

describe("overflowMessage", () => {
  it("sem mensagem da spec, diz a largura medida e a do aparelho", () => {
    expect(overflowMessage(497, 320)).toBe(
      "rolagem lateral: o documento tem 497 px de largura num aparelho de 320 px",
    );
  });

  it("a mensagem da spec prevalece sobre a padrão", () => {
    expect(overflowMessage(497, 320, "rolagem lateral na Bancada")).toBe(
      "rolagem lateral na Bancada",
    );
  });
});
