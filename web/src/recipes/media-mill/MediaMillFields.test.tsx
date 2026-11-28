import { render, screen, within } from "@testing-library/react";
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
    audio_bitrate_kbps: 192,
    trim_start: "",
    trim_end: "",
  },
  limits: {
    maxStreamIndex: 15,
    thumbnailMaxSide: 640,
    maxSampleRateHz: 48000,
    maxChannels: 2,
    maxTrimMs: 300000,
  },
  bitratesByFormat: {
    mp3: [128, 160, 192, 256, 320],
    aac: [96, 128, 160, 192, 256],
    opus: [64, 96, 128, 160, 192],
  },
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
const mp3 = () => screen.getByRole("radio", { name: "MP3 (com perda, toca em qualquer lugar)" });
const aac = () =>
  screen.getByRole("radio", { name: "AAC em M4A (com perda, padrão de celulares)" });
const opus = () =>
  screen.getByRole("radio", { name: "Opus (com perda, menor tamanho; sai em 48 kHz)" });
const bitrate = () => screen.getByRole("combobox", { name: "Qualidade do áudio, em kbps" });
const formatGroup = () => screen.getByRole("group", { name: "Formato do áudio" });
const track = () => screen.getByRole("textbox", { name: "Faixa de áudio" });
const trimStart = () => screen.getByRole("textbox", { name: "Início do trecho" });
const trimEnd = () => screen.getByRole("textbox", { name: "Fim do trecho" });
const three = () => screen.getByRole("radio", { name: "Três, em 10%, 50% e 90% da duração" });
const none = () => screen.getByRole("radio", { name: "Nenhuma" });
const bitrateOptions = () =>
  within(bitrate())
    .getAllByRole("option")
    .map((option) => option.textContent);

describe("MediaMillFields", () => {
  it("mostra os defaults e os limites do catálogo", () => {
    setup();

    expect(extract()).toBeChecked();
    expect(extract()).toHaveAccessibleDescription(/acima de 48 kHz ou de 2 canais/);
    expect(screen.getByRole("radio", { name: "WAV (PCM, sem compressão)" })).toBeChecked();
    expect(track()).toHaveValue("");
    expect(track()).toHaveAccessibleDescription(/de 0 a 15/);
    expect(screen.getByRole("radio", { name: "Uma, em 10% da duração" })).toBeChecked();
    expect(screen.getByRole("group", { name: "Miniaturas" })).toHaveAccessibleDescription(
      /640 pixels/,
    );
    expect(trimStart()).toHaveValue("");
    expect(trimEnd()).toHaveValue("");
    expect(trimEnd()).toHaveAccessibleDescription(
      "Vazio no fim vai até o final do vídeo. O trecho vale para o áudio e para as miniaturas.",
    );
  });

  it("a dica dos 16 bits vale só para WAV e FLAC", async () => {
    const { user } = setup();

    expect(formatGroup()).toHaveAccessibleDescription(/16 bits, uma conversão declarada/);

    await user.click(mp3());

    expect(formatGroup()).not.toHaveAccessibleDescription(/16 bits/);
  });

  it("o bitrate só habilita com formato com perda e diz por quê", async () => {
    const { user } = setup();

    expect(bitrate()).toBeDisabled();
    expect(bitrate()).toHaveAccessibleDescription(/Só vale para MP3, AAC e Opus/);

    await user.click(aac());

    expect(bitrate()).toBeEnabled();
    expect(bitrate()).toHaveValue("192");
  });

  it("o bitrate lista só os valores do formato e volta a 192 ao trocar de formato", async () => {
    const { user, lastDraft } = setup();

    await user.click(mp3());
    expect(bitrateOptions()).toEqual(["128", "160", "192", "256", "320"]);
    await user.selectOptions(bitrate(), "320");

    await user.click(aac());

    expect(bitrateOptions()).toEqual(["96", "128", "160", "192", "256"]);
    expect(bitrate()).toHaveValue("192");
    expect(lastDraft()).toMatchObject({
      ok: true,
      command: { parameters: { audio_format: "aac", audio_bitrate_kbps: 192 } },
    });
  });

  it("trocar de formato mantém o bitrate que a lista nova também tem", async () => {
    const { user } = setup();

    await user.click(mp3());
    await user.selectOptions(bitrate(), "128");
    await user.click(opus());

    expect(bitrate()).toHaveValue("128");
  });

  it("o Opus avisa que sai em 48 kHz", async () => {
    const { user } = setup();

    await user.click(opus());

    expect(formatGroup()).toHaveAccessibleDescription(/O Opus sai sempre em 48 kHz\./);
  });

  it("cada mudança entrega o comando atualizado", async () => {
    const { user, lastDraft } = setup();

    await user.click(flac());
    await user.type(track(), "2");
    await user.click(three());
    await user.type(trimStart(), "0:05");
    await user.type(trimEnd(), "1:15,5");

    expect(lastDraft()).toEqual({
      ok: true,
      command: {
        recipe_id: "media-mill",
        recipe_version: 2,
        parameters: {
          extract_audio: true,
          audio_format: "flac",
          audio_stream_index: 2,
          thumbnail_count: 3,
          audio_bitrate_kbps: 192,
          trim_start_ms: 5000,
          trim_end_ms: 75500,
        },
      },
    });
  });

  it("sem extrair áudio, formato, faixa e bitrate ficam visíveis mas desabilitados", async () => {
    const { user } = setup();

    await user.click(mp3());
    await user.click(extract());

    expect(flac()).toBeDisabled();
    expect(track()).toBeDisabled();
    expect(bitrate()).toBeDisabled();
    expect(trimStart()).toBeEnabled();
    expect(formatGroup()).toHaveAccessibleDescription(/Marque Extrair o áudio/);
    expect(bitrate()).toHaveAccessibleDescription(/Marque Extrair o áudio/);
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
          label: "Extrair o áudio",
          message: "Peça ao menos uma saída: extraia o áudio ou escolha uma ou três miniaturas.",
        },
      ],
    });
  });

  it("mostra o erro recebido no campo e liga ao controle", () => {
    setup(new Map([["media-mill-trim_end", "Fim inválido."]]));

    expect(trimEnd()).toBeInvalid();
    expect(trimEnd()).toHaveAccessibleDescription(/Fim inválido\./);
  });

  it("sem violações de acessibilidade", async () => {
    const { container } = setup();

    expect(await axeViolations(container)).toEqual([]);
  });
});
