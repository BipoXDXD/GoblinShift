import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeDraft, RecipeParameters } from "../types";
import type { GROUP_BY_OPTIONS, NAME_MODES } from "./schema";
import {
  downloadsDetoxField,
  parseDownloadsDetox,
  type DownloadsDetoxFormValues,
  type DownloadsDetoxSettings,
} from "./schema";

type Parameters = RecipeParameters<"downloads-detox">;

const SETTINGS: DownloadsDetoxSettings = {
  defaults: { deduplicate: true, group_by: "kind", name_mode: "preserve", name_prefix: "file" },
  limits: { maxPrefixChars: 40 },
};

function values(overrides: Partial<DownloadsDetoxFormValues> = {}): DownloadsDetoxFormValues {
  return { ...SETTINGS.defaults, ...overrides };
}

const indexed = (name_prefix: string) => values({ name_mode: "indexed", name_prefix });

function parametersOf(draft: RecipeDraft<"downloads-detox">) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

function issuesOf(draft: RecipeDraft<"downloads-detox">) {
  if (draft.ok) {
    throw new Error("rascunho deveria ser inválido");
  }
  return draft.issues;
}

describe("constantes espelham os tipos gerados", () => {
  it("cobrem cada união da spec", () => {
    expectTypeOf<(typeof GROUP_BY_OPTIONS)[number]>().toEqualTypeOf<
      NonNullable<Parameters["group_by"]>
    >();
    expectTypeOf<(typeof NAME_MODES)[number]>().toEqualTypeOf<
      NonNullable<Parameters["name_mode"]>
    >();
  });
});

describe("parseDownloadsDetox", () => {
  it("com nomes preservados, o prefixo não vai (a API o recusa nesse modo)", () => {
    expect(parseDownloadsDetox(values({ name_prefix: "qualquer/coisa" }), SETTINGS)).toEqual({
      ok: true,
      command: {
        recipe_id: "downloads-detox",
        recipe_version: 1,
        parameters: { deduplicate: true, group_by: "kind", name_mode: "preserve" },
      },
    });
  });

  it("com numeração, o prefixo vai junto", () => {
    expect(
      parametersOf(
        parseDownloadsDetox(
          values({ deduplicate: false, group_by: "none", name_mode: "indexed" }),
          SETTINGS,
        ),
      ),
    ).toEqual({ deduplicate: false, group_by: "none", name_mode: "indexed", name_prefix: "file" });
  });

  it.each([
    ["1 caractere", "a", true],
    ["no máximo", "x".repeat(40), true],
    ["acima do máximo", "x".repeat(41), false],
    ["ponto no meio", "foto.2026", true],
    ["espaço", "minhas fotos", true],
    ["acento", "fotografia-ção", true],
  ])("prefixo %s", (_, prefix, valid) => {
    expect(parseDownloadsDetox(indexed(prefix), SETTINGS).ok).toBe(valid);
  });

  it.each([
    ["vazio", "", "Informe o prefixo."],
    ["longo demais", "x".repeat(41), "Use no máximo 40 caracteres."],
    ["com barra", "a/b", "O prefixo não pode ter / nem \\."],
    ["com barra invertida", "a\\b", "O prefixo não pode ter / nem \\."],
    ["começando com ponto", ".oculto", "O prefixo não pode começar com ponto."],
    ["com controle", "a\u0000b", "O prefixo não pode ter caracteres invisíveis ou de controle."],
    [
      "com formato invisível",
      "a​b",
      "O prefixo não pode ter caracteres invisíveis ou de controle.",
    ],
  ])("prefixo %s é recusado no campo", (_, prefix, message) => {
    expect(issuesOf(parseDownloadsDetox(indexed(prefix), SETTINGS))).toEqual([
      { fieldId: "downloads-detox-name_prefix", label: "Prefixo da numeração", message },
    ]);
  });

  it("o máximo de caracteres segue o catálogo", () => {
    const settings = { ...SETTINGS, limits: { maxPrefixChars: 3 } };

    expect(parseDownloadsDetox(indexed("abc"), settings).ok).toBe(true);
    expect(parseDownloadsDetox(indexed("abcd"), settings).ok).toBe(false);
  });
});

describe("downloadsDetoxField", () => {
  it.each([
    [["deduplicate"], "downloads-detox-deduplicate", "Remover arquivos repetidos"],
    [["name_prefix"], "downloads-detox-name_prefix", "Prefixo da numeração"],
  ])("%j → %s (%s)", (path, fieldId, label) => {
    expect(downloadsDetoxField(path)).toEqual({ fieldId, label });
  });

  it.each([[[]], [["recipe_version"]], [["__proto__"]]])("caminho sem campo %j → null", (path) => {
    expect(downloadsDetoxField(path)).toBeNull();
  });
});
