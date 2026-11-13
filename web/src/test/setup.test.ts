import { getConfig, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

describe("configuração dos testes", () => {
  it("a espera do findBy acaba antes do tempo do teste", ({ task }) => {
    expect(getConfig().asyncUtilTimeout).toBeLessThan(task.timeout);
  });

  // Espera os 5 s inteiros do findBy de propósito: com o tempo do teste igual ao da espera, a falha
  // saía como "Test timed out", sem dizer o que faltou na tela.
  it("findBy sem o elemento falha com a mensagem do Testing Library", async () => {
    render(createElement("p", null, "Bancada"));

    await expect(screen.findByText("Elemento que não existe")).rejects.toThrow(
      /Unable to find an element with the text: Elemento que não existe/,
    );
  });
});
