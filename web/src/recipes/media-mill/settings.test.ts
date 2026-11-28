import { describe, expect, it } from "vitest";

import { catalogVersion } from "../../test/catalog";
import { mediaMillSettings } from "./settings";

const version = catalogVersion("media-mill");
const schema = version.parameters_schema;
// Os ramos do `allOf` da v2; o tipo do catálogo de exemplo é a união das receitas.
const bitrateRules = "allOf" in schema ? schema.allOf : [];

describe("mediaMillSettings", () => {
  it("defaults do catálogo viram os valores iniciais do formulário", () => {
    expect(mediaMillSettings(version)?.defaults).toEqual({
      extract_audio: true,
      audio_format: "wav",
      audio_stream_index: "",
      thumbnail_count: 1,
      audio_bitrate_kbps: 192,
      trim_start: "",
      trim_end: "",
    });
  });

  it("limites do catálogo e faixas do índice e do trecho no schema", () => {
    expect(mediaMillSettings(version)?.limits).toEqual({
      maxStreamIndex: 15,
      thumbnailMaxSide: 640,
      maxSampleRateHz: 48000,
      maxChannels: 2,
      maxTrimMs: 300000,
    });
  });

  it("lê os bitrates de cada formato com perda do allOf do schema", () => {
    expect(mediaMillSettings(version)?.bitratesByFormat).toEqual({
      mp3: [128, 160, 192, 256, 320],
      aac: [96, 128, 160, 192, 256],
      opus: [64, 96, 128, 160, 192],
    });
  });

  it("índice e trecho default viram texto", () => {
    const settings = mediaMillSettings({
      ...version,
      defaults: {
        ...version.defaults,
        audio_stream_index: 2,
        trim_start_ms: 75500,
        trim_end_ms: 90000,
      },
    });

    expect(settings?.defaults).toMatchObject({
      audio_stream_index: "2",
      trim_start: "1:15,5",
      trim_end: "1:30",
    });
  });

  it.each([
    ["miniaturas fora de 0, 1 e 3", { defaults: { ...version.defaults, thumbnail_count: 2 } }],
    ["formato desconhecido", { defaults: { ...version.defaults, audio_format: "ogg" } }],
    ["bitrate desconhecido", { defaults: { ...version.defaults, audio_bitrate_kbps: 100 } }],
    [
      "defaults da v1, sem bitrate nem trecho",
      { defaults: catalogVersion("media-mill", 1).defaults },
    ],
    ["limite ausente", { limits: { max_inputs: 5 } }],
    [
      "schema sem faixa inteira",
      {
        parameters_schema: {
          ...schema,
          properties: { ...schema.properties, audio_stream_index: {} },
        },
      },
    ],
    [
      "schema sem teto do trecho",
      { parameters_schema: { ...schema, properties: { ...schema.properties, trim_start_ms: {} } } },
    ],
    ["schema sem bitrates por formato", { parameters_schema: { ...schema, allOf: [] } }],
    [
      "schema sem um dos formatos com perda",
      { parameters_schema: { ...schema, allOf: bitrateRules.slice(1) } },
    ],
  ])("%s → null", (_, override) => {
    expect(mediaMillSettings({ ...version, ...override })).toBeNull();
  });
});
