import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DangerAlert } from "./DangerAlert";

describe("DangerAlert", () => {
  it("anuncia o conteúdo recebido como alerta", () => {
    render(
      <DangerAlert>
        <p>O download falhou.</p>
      </DangerAlert>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("O download falhou.");
  });
});
