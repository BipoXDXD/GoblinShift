import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeDraft, RecipeParameters } from "../types";
import type { AUDIO_FORMATS, BITRATES_KBPS, THUMBNAIL_COUNTS } from "./schema";
import {
  formatTrim,
  mediaMillField,
  parseMediaMill,
  parseTrim,
  type MediaMillFormValues,
  type MediaMillSettings,
} from "./schema";

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

function values(overrides: Partial<MediaMillFormValues> = {}): MediaMillFormValues {
  return { ...SETTINGS.defaults, ...overrides };
}

function parametersOf(draft: RecipeDraft<"media-mill">) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

function issuesOf(draft: RecipeDraft<"media-mill">) {
  if (draft.ok) {
    throw new Error("rascunho deveria ser inválido");
  }
  return draft.issues;
}

describe("constantes espelham os tipos gerados", () => {
  it("formatos de áudio, bitrates e quantidades de miniaturas", () => {
    expectTypeOf<(typeof AUDIO_FORMATS)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"media-mill">["audio_format"]>
    >();
    expectTypeOf<(typeof BITRATES_KBPS)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"media-mill">["audio_bitrate_kbps"]>
    >();
    expectTypeOf<(typeof THUMBNAIL_COUNTS)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"media-mill">["thumbnail_count"]>
    >();
  });
});

describe("parseTrim", () => {
  it.each([
    ["75", 75000],
    ["1:15", 75000],
    ["1:15,5", 75500],
    ["", null],
  ])("lê %j como %j ms", (text, ms) => {
    expect(parseTrim(text)).toBe(ms);
  });

  it.each([
    ["0:00,125", 125],
    [" 10:00 ", 600000],
    ["75,25", 75250],
  ])("aceita %j como %i ms", (text, ms) => {
    expect(parseTrim(text)).toBe(ms);
  });

  it.each(["1:5", "1:60", "1.5", "1:15.5", "1:15,1234", "-1", "abc", "1:2:3", ":15"])(
    "%j não é um ponto do vídeo",
    (text) => {
      expect(parseTrim(text)).toBeNull();
    },
  );
});

describe("formatTrim", () => {
  it.each([
    [0, "0:00"],
    [75000, "1:15"],
    [75500, "1:15,5"],
    [600125, "10:00,125"],
  ])("%i ms vira %j", (ms, text) => {
    expect(formatTrim(ms)).toBe(text);
  });
});

describe("parseMediaMill", () => {
  it("produz o comando da versão 2 com MP3 a 192 kbps e sem corte", () => {
    expect(parseMediaMill(values({ audio_format: "mp3" }), SETTINGS)).toEqual({
      ok: true,
      command: {
        recipe_id: "media-mill",
        recipe_version: 2,
        parameters: {
          extract_audio: true,
          audio_format: "mp3",
          audio_stream_index: null,
          thumbnail_count: 1,
          audio_bitrate_kbps: 192,
          trim_start_ms: 0,
          trim_end_ms: null,
        },
      },
    });
  });

  it("o trecho vai em milissegundos", () => {
    const draft = parseMediaMill(values({ trim_start: "0:30", trim_end: "1:15,5" }), SETTINGS);

    expect(parametersOf(draft)).toMatchObject({ trim_start_ms: 30000, trim_end_ms: 75500 });
  });

  it.each([
    ["0:30", "0:30"],
    ["1:00", "0:59"],
  ])("fim do corte igual ou antes do início aponta o campo (%s a %s)", (start, end) => {
    const issues = issuesOf(parseMediaMill(values({ trim_start: start, trim_end: end }), SETTINGS));

    expect(issues).toEqual([
      {
        fieldId: "media-mill-trim_end",
        label: "Fim do trecho",
        message: "O fim do trecho precisa vir depois do início.",
      },
    ]);
  });

  it.each([
    ["trim_start", "1.5", "media-mill-trim_start", "Início do trecho"],
    ["trim_end", "5:00,001", "media-mill-trim_end", "Fim do trecho"],
  ] as const)("%s %j fora do formato ou do limite aponta o campo", (field, text, id, label) => {
    const issues = issuesOf(parseMediaMill(values({ [field]: text }), SETTINGS));

    expect(issues).toEqual([
      {
        fieldId: id,
        label,
        message: "Use m:ss, como 1:15, ou segundos, como 75. O máximo é 5:00.",
      },
    ]);
  });

  it("formato sem perda leva o bitrate default, sem cobrar o que está desabilitado", () => {
    const draft = parseMediaMill(
      values({ audio_format: "flac", audio_bitrate_kbps: 320 }),
      SETTINGS,
    );

    expect(parametersOf(draft)).toMatchObject({ audio_format: "flac", audio_bitrate_kbps: 192 });
  });

  it("bitrate fora da lista do formato é recusado no campo", () => {
    const issues = issuesOf(
      parseMediaMill(values({ audio_format: "aac", audio_bitrate_kbps: 320 }), SETTINGS),
    );

    expect(issues).toEqual([
      {
        fieldId: "media-mill-audio_bitrate_kbps",
        label: "Qualidade do áudio, em kbps",
        message: "Escolha uma das opções.",
      },
    ]);
  });

  it.each([
    ["0", 0],
    [" 15 ", 15],
  ])("faixa %j vira o número %i", (text, index) => {
    const draft = parseMediaMill(values({ audio_stream_index: text }), SETTINGS);

    expect(parametersOf(draft).audio_stream_index).toBe(index);
  });

  it.each(["16", "-1", "1.5", "abc", "1e1"])("faixa %j é recusada no campo", (text) => {
    const issues = issuesOf(parseMediaMill(values({ audio_stream_index: text }), SETTINGS));

    expect(issues).toEqual([
      {
        fieldId: "media-mill-audio_stream_index",
        label: "Faixa de áudio",
        message: "Deixe vazio ou informe um número inteiro de 0 a 15.",
      },
    ]);
  });

  it("sem áudio, formato, faixa e bitrate voltam aos defaults e o trecho continua", () => {
    const draft = parseMediaMill(
      values({
        extract_audio: false,
        audio_format: "opus",
        audio_stream_index: "abc",
        audio_bitrate_kbps: 64,
        trim_start: "0:10",
      }),
      SETTINGS,
    );

    expect(parametersOf(draft)).toEqual({
      extract_audio: false,
      audio_format: "wav",
      audio_stream_index: null,
      thumbnail_count: 1,
      audio_bitrate_kbps: 192,
      trim_start_ms: 10000,
      trim_end_ms: null,
    });
  });

  it("sem áudio e sem miniaturas, o erro aponta a extração", () => {
    const issues = issuesOf(
      parseMediaMill(values({ extract_audio: false, thumbnail_count: 0 }), SETTINGS),
    );

    expect(issues).toEqual([
      {
        fieldId: "media-mill-extract_audio",
        label: "Extrair o áudio",
        message: "Peça ao menos uma saída: extraia o áudio ou escolha uma ou três miniaturas.",
      },
    ]);
  });

  it("só áudio, sem miniaturas, é válido", () => {
    expect(parametersOf(parseMediaMill(values({ thumbnail_count: 0 }), SETTINGS))).toMatchObject({
      extract_audio: true,
      thumbnail_count: 0,
    });
  });
});

describe("mediaMillField", () => {
  it.each([
    [["audio_stream_index"], "media-mill-audio_stream_index", "Faixa de áudio"],
    [["thumbnail_count"], "media-mill-thumbnail_count", "Miniaturas"],
    [["audio_bitrate_kbps"], "media-mill-audio_bitrate_kbps", "Qualidade do áudio, em kbps"],
    [["trim_start_ms"], "media-mill-trim_start", "Início do trecho"],
    [["trim_end_ms"], "media-mill-trim_end", "Fim do trecho"],
  ])("%j → %s (%s)", (path, fieldId, label) => {
    expect(mediaMillField(path)).toEqual({ fieldId, label });
  });

  it.each([[[]], [["inventado"]]])("caminho sem campo %j → null", (path) => {
    expect(mediaMillField(path)).toBeNull();
  });
});
