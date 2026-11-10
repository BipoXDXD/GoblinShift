import { screen, waitFor } from "@testing-library/react";
import { expect } from "vitest";

/**
 * Espera o `h1` com este nome receber o foco. Com as telas carregadas por rota, o título aparece
 * fora de um `act` e o efeito do FocusHeading roda depois: um `findBy` seguido de `toHaveFocus`
 * pode olhar o foco antes do efeito.
 */
export async function expectHeadingFocused(name: string): Promise<void> {
  await waitFor(() => {
    expect(screen.getByRole("heading", { level: 1, name })).toHaveFocus();
  });
}

/**
 * Grava a descrição que o leitor de tela leria quando o campo recebe o foco: o texto dos elementos
 * do `aria-describedby` naquele instante, antes de qualquer render que venha depois.
 */
export function recordDescriptionOnFocus(control: HTMLElement): {
  readonly descriptions: string[];
} {
  const descriptions: string[] = [];
  control.addEventListener("focus", () => {
    const ids = (control.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean);
    descriptions.push(ids.map((id) => document.getElementById(id)?.textContent ?? "").join(" "));
  });
  return { descriptions };
}
