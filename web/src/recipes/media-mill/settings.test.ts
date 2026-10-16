import { describe, expect, it } from "vitest";

import { catalogVersion } from "../../test/catalog";
import { mediaMillSettings } from "./settings";

const version = catalogVersion("media-mill");

describe("mediaMillSettings", () => {
  it("defaults do catálogo viram os valores iniciais do formulário", () => {
    expect(mediaMillSettings(version)?.defaults).toEqual({
      extract_audio: true,
      audio_format: "wav",
      audio_stream_index: "",
      thumbnail_count: 1,
    });
  });

  it("limites do catálogo e faixa do índice no schema", () => {
    expect(mediaMillSettings(version)?.limits).toEqual({
      maxStreamIndex: 15,
      thumbnailMaxSide: 640,
      maxSampleRateHz: 48000,
      maxChannels: 2,
    });
  });

  it("índice default numérico vira texto", () => {
    const settings = mediaMillSettings({
      ...version,
      defaults: { ...version.defaults, audio_stream_index: 2 },
    });

    expect(settings?.defaults.audio_stream_index).toBe("2");
  });

  it.each([
    ["miniaturas fora de 0, 1 e 3", { defaults: { ...version.defaults, thumbnail_count: 2 } }],
    ["formato desconhecido", { defaults: { ...version.defaults, audio_format: "mp3" } }],
    ["limite ausente", { limits: { max_inputs: 5 } }],
    ["schema sem faixa inteira", { parameters_schema: { properties: {} } }],
  ])("%s → null", (_, override) => {
    expect(mediaMillSettings({ ...version, ...override })).toBeNull();
  });
});
