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
