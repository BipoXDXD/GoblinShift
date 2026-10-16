import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeDraft, RecipeParameters } from "../types";
import type { AUDIO_FORMATS, THUMBNAIL_COUNTS } from "./schema";
import {
  mediaMillFieldId,
  parseMediaMill,
  type MediaMillFormValues,
  type MediaMillSettings,
} from "./schema";

const SETTINGS: MediaMillSettings = {
  defaults: {
    extract_audio: true,
    audio_format: "wav",
    audio_stream_index: "",
    thumbnail_count: 1,
  },
  limits: { maxStreamIndex: 15, thumbnailMaxSide: 640, maxSampleRateHz: 48000, maxChannels: 2 },
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
  it("formatos de áudio e quantidades de miniaturas", () => {
    expectTypeOf<(typeof AUDIO_FORMATS)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"media-mill">["audio_format"]>
    >();
    expectTypeOf<(typeof THUMBNAIL_COUNTS)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"media-mill">["thumbnail_count"]>
    >();
  });
});

describe("parseMediaMill", () => {
  it("os defaults viram um comando explícito, com a primeira faixa como null", () => {
    expect(parseMediaMill(values(), SETTINGS)).toEqual({
      ok: true,
      command: {
        recipe_id: "media-mill",
        recipe_version: 1,
        parameters: {
          extract_audio: true,
          audio_format: "wav",
          audio_stream_index: null,
          thumbnail_count: 1,
        },
      },
    });
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
        message: "Deixe vazio ou informe um número inteiro de 0 a 15.",
      },
    ]);
  });

  it("sem áudio, formato e faixa voltam aos defaults e não são cobrados", () => {
    const draft = parseMediaMill(
      values({ extract_audio: false, audio_format: "flac", audio_stream_index: "abc" }),
      SETTINGS,
    );

    expect(parametersOf(draft)).toEqual({
      extract_audio: false,
      audio_format: "wav",
      audio_stream_index: null,
      thumbnail_count: 1,
    });
  });

  it("sem áudio e sem miniaturas, o erro aponta a extração", () => {
    const issues = issuesOf(
      parseMediaMill(values({ extract_audio: false, thumbnail_count: 0 }), SETTINGS),
    );

    expect(issues).toEqual([
      {
        fieldId: "media-mill-extract_audio",
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

describe("mediaMillFieldId", () => {
  it.each([
    [["audio_stream_index"], "media-mill-audio_stream_index"],
    [["thumbnail_count"], "media-mill-thumbnail_count"],
    [[], null],
    [["inventado"], null],
  ])("%j → %s", (path, id) => {
    expect(mediaMillFieldId(path)).toBe(id);
  });
});
