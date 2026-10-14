import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunStatus } from "../../runs/api";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each<[RunStatus, string]>([
    ["queued", "Na fila"],
    ["running", "Em andamento"],
    ["stopping", "Cancelando"],
    ["succeeded", "Concluída"],
    ["succeeded_with_warnings", "Concluída com avisos"],
    ["canceled", "Cancelada"],
    ["failed", "Falhou"],
  ])("%s diz o estado em texto, com um ícone decorativo ao lado", (status, text) => {
    const { container } = render(<StatusBadge status={status} />);

    expect(screen.getByText(text)).toBeInTheDocument();
    const icon = container.querySelector("svg");
    expect(icon).toHaveAttribute("aria-hidden", "true");
  });

  it("estados diferentes têm ícones diferentes, não só cor", () => {
    const icons = (["succeeded", "failed", "queued"] as const).map((status) => {
      const { container, unmount } = render(<StatusBadge status={status} />);
      const markup = container.querySelector("svg")?.innerHTML ?? "";
      unmount();
      return markup;
    });

    expect(new Set(icons).size).toBe(3);
  });
});
