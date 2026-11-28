import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { SystemPreset } from "../catalog/api";
import { axeViolations } from "../test/axe";
import { catalogVersion } from "../test/catalog";
import { SystemPresets } from "./SystemPresets";

const PRESETS: readonly SystemPreset[] = catalogVersion("image-forge").system_presets;

describe("SystemPresets", () => {
  it("uma linha de botões com o título e a dica", () => {
    render(<SystemPresets presets={PRESETS} onApply={vi.fn()} />);

    const group = screen.getByRole("group", { name: "Ajustes prontos" });
    expect(group).toHaveAccessibleDescription(
      "Preenchem o formulário; você pode mudar antes de executar.",
    );
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Web 1600",
      "E-mail 1024",
      "Redes 1080",
    ]);
  });

  it("clicar entrega o ajuste escolhido", async () => {
    const onApply = vi.fn<(preset: SystemPreset) => void>();
    const user = userEvent.setup();
    render(<SystemPresets presets={PRESETS} onApply={onApply} />);

    await user.click(screen.getByRole("button", { name: "Redes 1080" }));

    expect(onApply).toHaveBeenCalledExactlyOnceWith(PRESETS[2]);
  });

  it("sem ajustes, não mostra nada", () => {
    const { container } = render(<SystemPresets presets={[]} onApply={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("não tem violações do axe", async () => {
    render(<SystemPresets presets={PRESETS} onApply={vi.fn()} />);

    expect(await axeViolations(document.body)).toEqual([]);
  });
});
