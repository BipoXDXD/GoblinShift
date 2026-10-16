import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import type { RecipeDraft } from "../types";
import { ImageForgeFields } from "./ImageForgeFields";
import type { ImageForgeSettings } from "./schema";

const SETTINGS: ImageForgeSettings = {
  defaults: {
    max_width: "1200",
    max_height: "900",
    output_format: "preserve",
    jpeg_quality: "85",
    jpeg_background: "#FFFFFF",
    make_thumbnails: false,
  },
  limits: {
    width: { min: 16, max: 4096 },
    height: { min: 16, max: 2048 },
    quality: { min: 60, max: 95 },
    thumbnailBox: 320,
  },
};

function setup(errors: ReadonlyMap<string, string> = new Map()) {
  const onDraftChange = vi.fn<(draft: RecipeDraft<"image-forge">) => void>();
  const user = userEvent.setup();
  render(
    <ImageForgeFields
      settings={SETTINGS}
      errors={errors}
      onDraftChange={onDraftChange}
      inputs={[]}
    />,
  );
  const lastDraft = (): RecipeDraft<"image-forge"> => {
    const draft = onDraftChange.mock.lastCall?.[0];
    if (draft === undefined) {
      throw new Error("onDraftChange não foi chamado");
    }
    return draft;
  };
  return { user, lastDraft };
}

function parametersOf(draft: RecipeDraft<"image-forge">) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

const width = () => screen.getByRole("textbox", { name: "Largura máxima, em pixels" });
const quality = () => screen.queryByRole("textbox", { name: "Qualidade do JPEG" });

describe("ImageForgeFields", () => {
  it("mostra os defaults e as faixas do catálogo", () => {
    setup();

    expect(width()).toHaveValue("1200");
    expect(screen.getByRole("textbox", { name: "Altura máxima, em pixels" })).toHaveValue("900");
    expect(
      screen.getByRole("textbox", { name: "Altura máxima, em pixels" }),
    ).toHaveAccessibleDescription(/De 16 a 2048\./);
    expect(quality()).toHaveAccessibleDescription(/De 60 a 95\./);
    expect(screen.getByRole("checkbox", { name: "Gerar miniaturas" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Gerar miniaturas" })).toHaveAccessibleDescription(
      /320 pixels/,
    );
  });

  it("cada mudança entrega o comando atualizado", async () => {
    const { user, lastDraft } = setup();

    await user.clear(width());
    await user.type(width(), "800");
    await user.selectOptions(screen.getByRole("combobox", { name: "Formato de saída" }), "jpeg");
    await user.click(screen.getByRole("checkbox", { name: "Gerar miniaturas" }));

    expect(parametersOf(lastDraft())).toEqual({
      max_width: 800,
      max_height: 900,
      output_format: "jpeg",
      jpeg_quality: 85,
      jpeg_background: "#FFFFFF",
      make_thumbnails: true,
    });
  });

  it("valor fora da faixa entrega o erro do campo", async () => {
    const { user, lastDraft } = setup();

    await user.clear(width());
    await user.type(width(), "5000");

    expect(lastDraft()).toEqual({
      ok: false,
      issues: [
        { fieldId: "image-forge-max_width", message: "Informe um número inteiro de 16 a 4096." },
      ],
    });
  });

  it("saída PNG esconde as opções de JPEG e manda os defaults delas", async () => {
    const { user, lastDraft } = setup();
    const background = screen.getByRole("textbox", {
      name: "Cor de fundo da transparência no JPEG",
    });
    await user.clear(background);
    await user.type(background, "#000000");

    await user.selectOptions(screen.getByRole("combobox", { name: "Formato de saída" }), "png");

    expect(quality()).toBeNull();
    expect(
      screen.queryByRole("textbox", { name: "Cor de fundo da transparência no JPEG" }),
    ).toBeNull();
    expect(parametersOf(lastDraft())).toMatchObject({
      output_format: "png",
      jpeg_quality: 85,
      jpeg_background: "#FFFFFF",
    });
  });

  it("mostra o erro recebido no campo certo", () => {
    setup(new Map([["image-forge-jpeg_background", "Cor recusada."]]));

    expect(
      screen.getByRole("textbox", { name: "Cor de fundo da transparência no JPEG" }),
    ).toHaveAccessibleDescription(/Erro: Cor recusada\./);
  });

  it("não tem violações do axe", async () => {
    setup(new Map([["image-forge-max_width", "Valor recusado."]]));

    expect(await axeViolations(document.body)).toEqual([]);
  });
});
