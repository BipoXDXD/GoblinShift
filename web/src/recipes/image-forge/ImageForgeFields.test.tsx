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
    fit: "contain",
    scale_percent: "100",
    webp_quality: "80",
    webp_lossless: false,
  },
  disabledValues: {
    max_width: "1600",
    max_height: "1600",
    jpeg_quality: "85",
    jpeg_background: "#FFFFFF",
    scale_percent: "100",
    webp_quality: "80",
    webp_lossless: false,
  },
  limits: {
    width: { min: 16, max: 4096 },
    height: { min: 16, max: 2048 },
    quality: { min: 60, max: 95 },
    scale: { min: 1, max: 100 },
    webpQuality: { min: 60, max: 95 },
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
const height = () => screen.getByRole("textbox", { name: "Altura máxima, em pixels" });
const quality = () => screen.queryByRole("textbox", { name: "Qualidade do JPEG" });
const scale = () => screen.getByRole("textbox", { name: "Escala, em %" });
const webpQuality = () => screen.getByRole("textbox", { name: "Qualidade do WebP" });
const webpLossless = () => screen.getByRole("checkbox", { name: "WebP sem perdas" });
const format = () => screen.getByRole("combobox", { name: "Formato de saída" });

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
      fit: "contain",
      scale_percent: 100,
      webp_quality: 80,
      webp_lossless: false,
    });
  });

  it("o grupo Ajuste vem logo depois do formato, com as dicas de cortar e de margem", () => {
    setup();

    const group = screen.getByRole("group", { name: "Ajuste" });
    const radios = screen.getAllByRole("radio");
    expect(radios.map((radio) => radio.getAttribute("value"))).toEqual([
      "contain",
      "cover",
      "pad",
      "scale",
    ]);
    expect(format().compareDocumentPosition(group)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(
      screen.getByRole("radio", { name: "Caber na caixa (mantém a proporção)" }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", { name: "Preencher e cortar o centro" }),
    ).toHaveAccessibleDescription(
      "Corta o que sobra nas bordas para preencher a caixa; nunca amplia.",
    );
    expect(
      screen.getByRole("radio", { name: "Caixa exata com margem" }),
    ).toHaveAccessibleDescription(
      "A margem fica transparente em PNG e WebP, e na cor de fundo em JPEG.",
    );
  });

  it("WebP entra no formato de saída e Manter diz o que sai", () => {
    setup();

    expect(screen.getByRole("option", { name: "WebP" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", {
        name: "Manter o formato (PNG e GIF saem PNG; JPEG sai JPEG; WebP sai WebP)",
      }),
    ).toBeInTheDocument();
  });

  it("em escala, largura e altura ficam desabilitadas e saem no default", async () => {
    const { user, lastDraft } = setup();
    expect(scale()).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: "Escala em porcentagem" }));
    await user.clear(scale());
    await user.type(scale(), "50");

    expect(width()).toBeDisabled();
    expect(height()).toBeDisabled();
    expect(scale()).toBeEnabled();
    expect(parametersOf(lastDraft())).toMatchObject({
      fit: "scale",
      scale_percent: 50,
      max_width: 1600,
      max_height: 1600,
    });
  });

  it("fora de escala, a escala fica desabilitada e sai no default", async () => {
    const { user, lastDraft } = setup();
    await user.click(screen.getByRole("radio", { name: "Escala em porcentagem" }));
    await user.clear(scale());
    await user.type(scale(), "50");

    await user.click(screen.getByRole("radio", { name: "Preencher e cortar o centro" }));

    expect(scale()).toBeDisabled();
    expect(width()).toBeEnabled();
    expect(parametersOf(lastDraft())).toMatchObject({
      fit: "cover",
      scale_percent: 100,
      max_width: 1200,
      max_height: 900,
    });
  });

  it("qualidade do WebP só habilita com saída WebP ou manter, e some com sem perda", async () => {
    const { user, lastDraft } = setup();
    expect(webpQuality()).toBeEnabled();

    await user.selectOptions(format(), "jpeg");
    expect(webpQuality()).toBeDisabled();
    expect(webpLossless()).toBeDisabled();

    await user.selectOptions(format(), "webp");
    expect(webpQuality()).toBeEnabled();
    await user.clear(webpQuality());
    await user.type(webpQuality(), "70");
    await user.click(webpLossless());

    expect(webpQuality()).toBeDisabled();
    expect(parametersOf(lastDraft())).toMatchObject({
      output_format: "webp",
      webp_lossless: true,
      webp_quality: 80,
    });
  });

  it("saída WebP esconde as opções de JPEG", async () => {
    const { user } = setup();

    await user.selectOptions(format(), "webp");

    expect(quality()).toBeNull();
  });

  it("valor fora da faixa entrega o erro do campo", async () => {
    const { user, lastDraft } = setup();

    await user.clear(width());
    await user.type(width(), "5000");

    expect(lastDraft()).toEqual({
      ok: false,
      issues: [
        {
          fieldId: "image-forge-max_width",
          label: "Largura máxima, em pixels",
          message: "Informe um número inteiro de 16 a 4096.",
        },
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

  it("não tem violações de axe com o modo escala aberto", async () => {
    const { user } = setup(new Map([["image-forge-scale_percent", "Valor recusado."]]));

    await user.click(screen.getByRole("radio", { name: "Escala em porcentagem" }));

    expect(await axeViolations(document.body)).toEqual([]);
  });
});
