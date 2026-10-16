import { describe, expectTypeOf, it } from "vitest";

import type { RunCommand } from "./types";

// Trava o formato que o gerador produz para o corpo de POST /runs: `allOf` (campos comuns) +
// `oneOf` das versões do catálogo, discriminado por `recipe_id`. Uma receita nova na API
// muda este teste de propósito, para o formulário dela ser escrito à mão (2.2).
type Variant<K extends RunCommand["recipe_id"]> = Extract<RunCommand, { recipe_id: K }>;

describe("contrato gerado de POST /runs", () => {
  it("discrimina as variantes pelo recipe_id literal", () => {
    expectTypeOf<RunCommand["recipe_id"]>().toEqualTypeOf<
      "csv-janitor" | "image-forge" | "downloads-detox" | "pdf-press" | "media-mill"
    >();
  });

  it("fixa a versão de cada variante como literal", () => {
    expectTypeOf<Variant<"csv-janitor">["recipe_version"]>().toEqualTypeOf<1>();
    expectTypeOf<Variant<"image-forge">["recipe_version"]>().toEqualTypeOf<1>();
    expectTypeOf<Variant<"downloads-detox">["recipe_version"]>().toEqualTypeOf<1>();
    expectTypeOf<Variant<"pdf-press">["recipe_version"]>().toEqualTypeOf<1>();
    expectTypeOf<Variant<"media-mill">["recipe_version"]>().toEqualTypeOf<1>();
  });

  it("dá à CSV Janitor os próprios parâmetros, sem index signature", () => {
    type Parameters = Variant<"csv-janitor">["parameters"];

    expectTypeOf<Parameters["input_delimiter"]>().toEqualTypeOf<"," | ";" | "\t" | undefined>();
    expectTypeOf<keyof Parameters>().toEqualTypeOf<
      | "input_delimiter"
      | "output_delimiter"
      | "trim_columns"
      | "case_rules"
      | "filters"
      | "deduplicate_by"
      | "output_columns"
      | "formula_guard"
    >();
  });

  it("dá ao Image Forge os próprios parâmetros, sem index signature", () => {
    type Parameters = Variant<"image-forge">["parameters"];

    expectTypeOf<Parameters["output_format"]>().toEqualTypeOf<
      "preserve" | "jpeg" | "png" | undefined
    >();
    expectTypeOf<Parameters["max_width"]>().toEqualTypeOf<number | undefined>();
    expectTypeOf<keyof Parameters>().toEqualTypeOf<
      | "max_width"
      | "max_height"
      | "output_format"
      | "jpeg_quality"
      | "jpeg_background"
      | "make_thumbnails"
    >();
  });

  it("dá ao Downloads Detox os próprios parâmetros, sem index signature", () => {
    type Parameters = Variant<"downloads-detox">["parameters"];

    expectTypeOf<Parameters["group_by"]>().toEqualTypeOf<"kind" | "none" | undefined>();
    expectTypeOf<Parameters["name_mode"]>().toEqualTypeOf<"preserve" | "indexed" | undefined>();
    expectTypeOf<keyof Parameters>().toEqualTypeOf<
      "deduplicate" | "group_by" | "name_mode" | "name_prefix"
    >();
  });

  it("dá ao PDF Press os próprios parâmetros, com a seleção por upload e página", () => {
    type Parameters = Variant<"pdf-press">["parameters"];

    expectTypeOf<Parameters["operation"]>().toEqualTypeOf<
      "merge" | "split" | "select" | undefined
    >();
    expectTypeOf<Parameters["select"]>().toEqualTypeOf<
      { upload_id: string; page_number: number }[] | null | undefined
    >();
    expectTypeOf<keyof Parameters>().toEqualTypeOf<"operation" | "pages_per_document" | "select">();
  });

  it("dá ao Media Mill os próprios parâmetros, sem index signature", () => {
    type Parameters = Variant<"media-mill">["parameters"];

    expectTypeOf<Parameters["audio_format"]>().toEqualTypeOf<"wav" | "flac" | undefined>();
    expectTypeOf<Parameters["audio_stream_index"]>().toEqualTypeOf<number | null | undefined>();
    expectTypeOf<Parameters["thumbnail_count"]>().toEqualTypeOf<0 | 1 | 3 | undefined>();
    expectTypeOf<keyof Parameters>().toEqualTypeOf<
      "extract_audio" | "audio_format" | "audio_stream_index" | "thumbnail_count"
    >();
  });

  it("mantém os campos comuns fora das variantes", () => {
    expectTypeOf<RunCommand["input_upload_ids"]>().toEqualTypeOf<string[] | undefined>();
  });
});
