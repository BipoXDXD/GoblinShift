import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import type { RecipeDraft } from "../types";
import { MediaMillFields } from "./MediaMillFields";
import type { MediaMillSettings } from "./schema";

const SETTINGS: MediaMillSettings = {
  defaults: {
    extract_audio: true,
    audio_format: "wav",
    audio_stream_index: "",
    thumbnail_count: 1,
  },
  limits: { maxStreamIndex: 15, thumbnailMaxSide: 640, maxSampleRateHz: 48000, maxChannels: 2 },
};

function setup(errors: ReadonlyMap<string, string> = new Map()) {
  const onDraftChange = vi.fn<(draft: RecipeDraft<"media-mill">) => void>();
  const user = userEvent.setup();
  const view = render(
    <MediaMillFields
      settings={SETTINGS}
      errors={errors}
      onDraftChange={onDraftChange}
      inputs={[]}
    />,
  );
  const lastDraft = (): RecipeDraft<"media-mill"> => {
    const draft = onDraftChange.mock.lastCall?.[0];
    if (draft === undefined) {
      throw new Error("onDraftChange não foi chamado");
    }
    return draft;
  };
  return { user, lastDraft, container: view.container };
}

const extract = () => screen.getByRole("checkbox", { name: "Extrair o áudio" });
const flac = () => screen.getByRole("radio", { name: "FLAC (compressão sem perdas)" });
const track = () => screen.getByRole("textbox", { name: "Faixa de áudio" });
const three = () => screen.getByRole("radio", { name: "Três, em 10%, 50% e 90% da duração" });
const none = () => screen.getByRole("radio", { name: "Nenhuma" });

describe("MediaMillFields", () => {
  it("mostra os defaults e os limites do catálogo", () => {
    setup();

    expect(extract()).toBeChecked();
    expect(extract()).toHaveAccessibleDescription(/sem reamostrar nem misturar canais/);
    expect(extract()).toHaveAccessibleDescription(/acima de 48 kHz ou de 2 canais/);
    expect(screen.getByRole("radio", { name: "WAV (PCM, sem compressão)" })).toBeChecked();
    expect(track()).toHaveValue("");
    expect(track()).toHaveAccessibleDescription(/de 0 a 15/);
    expect(screen.getByRole("radio", { name: "Uma, em 10% da duração" })).toBeChecked();
    expect(screen.getByRole("group", { name: "Miniaturas" })).toHaveAccessibleDescription(
      /640 pixels/,
    );
    expect(screen.getByRole("group", { name: "Formato do áudio" })).toHaveAccessibleDescription(
      /16 bits, uma conversão declarada/,
    );
  });

  it("cada mudança entrega o comando atualizado", async () => {
    const { user, lastDraft } = setup();

    await user.click(flac());
    await user.type(track(), "2");
    await user.click(three());

    expect(lastDraft()).toEqual({
      ok: true,
      command: {
        recipe_id: "media-mill",
        recipe_version: 1,
        parameters: {
          extract_audio: true,
          audio_format: "flac",
          audio_stream_index: 2,
          thumbnail_count: 3,
        },
      },
    });
  });

  it("sem extrair áudio, formato e faixa ficam visíveis mas desabilitados", async () => {
    const { user } = setup();

    await user.click(extract());

    expect(flac()).toBeDisabled();
    expect(track()).toBeDisabled();
    expect(screen.getByRole("group", { name: "Formato do áudio" })).toHaveAccessibleDescription(
      /Marque Extrair o áudio/,
    );
  });

  it("sem nenhuma saída o rascunho é inválido na extração", async () => {
    const { user, lastDraft } = setup();

    await user.click(extract());
    await user.click(none());

    expect(lastDraft()).toEqual({
      ok: false,
      issues: [
        {
          fieldId: "media-mill-extract_audio",
          message: "Peça ao menos uma saída: extraia o áudio ou escolha uma ou três miniaturas.",
        },
      ],
    });
  });

  it("mostra o erro recebido no campo e liga ao controle", () => {
    setup(new Map([["media-mill-audio_stream_index", "Faixa inválida."]]));

    expect(track()).toBeInvalid();
    expect(track()).toHaveAccessibleDescription(/Faixa inválida\./);
  });

  it("sem violações de acessibilidade", async () => {
    const { container } = setup();

    expect(await axeViolations(container)).toEqual([]);
  });
});
