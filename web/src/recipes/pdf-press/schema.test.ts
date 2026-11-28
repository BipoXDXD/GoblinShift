import { describe, expect, expectTypeOf, it } from "vitest";

import { TEXT } from "../../text";
import type { RecipeDraft, RecipeInput, RecipeParametersAt } from "../types";
import {
  cardFieldId,
  pageMapPreview,
  parsePdfPress,
  pdfPressField,
  type COMPRESSIONS,
  type OPERATIONS,
  type PageCard,
  type PdfPressFormValues,
  type PdfPressSettings,
  type ROTATIONS,
  type Rotation,
} from "./schema";

type Parameters = RecipeParametersAt<"pdf-press", 2>;

const SETTINGS: PdfPressSettings = {
  defaults: {
    operation: "merge",
    pages_per_document: "1",
    rotation: 0,
    compression: "none",
    strip_metadata: true,
    cards: [],
  },
  limits: { maxPages: 200, maxRanges: 4, maxOutputPages: 400 },
};
const A: RecipeInput = { uploadId: "4eb3e442-3f30-4d83-8d34-cb18a99a46cb", name: "a.pdf" };
const B: RecipeInput = { uploadId: "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2", name: "b.pdf" };
const COMMON = { compression: "none", strip_metadata: true } as const;

function values(overrides: Partial<PdfPressFormValues> = {}): PdfPressFormValues {
  return { ...SETTINGS.defaults, ...overrides };
}

function card(input: RecipeInput | null, pages: string, rotation: Rotation = 0): PageCard {
  return { key: crypto.randomUUID(), uploadId: input?.uploadId ?? "", pages, rotation };
}

function parametersOf(draft: RecipeDraft<"pdf-press">) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

function issuesOf(draft: RecipeDraft<"pdf-press">) {
  if (draft.ok) {
    throw new Error("rascunho deveria ser inválido");
  }
  return draft.issues;
}

describe("constantes espelham os tipos gerados", () => {
  it("cobrem as uniões da spec da v2", () => {
    expectTypeOf<(typeof OPERATIONS)[number]>().toEqualTypeOf<
      NonNullable<Parameters["operation"]>
    >();
    expectTypeOf<(typeof ROTATIONS)[number]>().toEqualTypeOf<NonNullable<Parameters["rotation"]>>();
    expectTypeOf<(typeof COMPRESSIONS)[number]>().toEqualTypeOf<
      NonNullable<Parameters["compression"]>
    >();
  });
});

describe("parsePdfPress", () => {
  it("merge manda a operação, a rotação, a compressão e o strip_metadata, na versão 2", () => {
    expect(parsePdfPress(values({ compression: "lossless" }), SETTINGS, [A])).toEqual({
      ok: true,
      command: {
        recipe_id: "pdf-press",
        recipe_version: 2,
        parameters: {
          operation: "merge",
          rotation: 0,
          compression: "lossless",
          strip_metadata: true,
        },
      },
    });
  });

  it("split manda o tamanho do grupo como número e a rotação global", () => {
    const draft = parsePdfPress(
      values({ operation: "split", pages_per_document: " 3 ", rotation: 270 }),
      SETTINGS,
      [],
    );

    expect(parametersOf(draft)).toEqual({
      operation: "split",
      pages_per_document: 3,
      rotation: 270,
      ...COMMON,
    });
  });

  it("rotação global só vai no comando de merge e split", () => {
    const draft = parsePdfPress(
      values({ operation: "select", rotation: 90, cards: [card(A, "1")] }),
      SETTINGS,
      [A],
    );

    expect(parametersOf(draft)).not.toHaveProperty("rotation");
  });

  it.each(["0", "201", "2.5", "", "dois"])("split recusa o grupo %j", (size) => {
    const draft = parsePdfPress(
      values({ operation: "split", pages_per_document: size }),
      SETTINGS,
      [],
    );

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-pages_per_document",
        label: "Páginas por parte",
        message: "Informe um número inteiro de 1 a 200.",
      },
    ]);
  });

  it("um cartão com 1-3, 7, 10- vira três intervalos do mesmo PDF e rotação", () => {
    expect(
      parsePdfPress(
        values({ operation: "select", cards: [card(A, "1-3, 7, 10-", 90)] }),
        SETTINGS,
        [A],
      ),
    ).toEqual({
      ok: true,
      command: {
        recipe_id: "pdf-press",
        recipe_version: 2,
        parameters: expect.objectContaining({
          select: [
            { upload_id: A.uploadId, first_page: 1, last_page: 3, rotation: 90 },
            { upload_id: A.uploadId, first_page: 7, last_page: 7, rotation: 90 },
            { upload_id: A.uploadId, first_page: 10, last_page: null, rotation: 90 },
          ],
        }) as unknown,
      },
    });
  });

  it("select manda os cartões na ordem, com repetições", () => {
    const cards = [card(B, "2"), card(A, "1", 180), card(B, "2")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A, B]);

    expect(parametersOf(draft)).toEqual({
      operation: "select",
      select: [
        { upload_id: B.uploadId, first_page: 2, last_page: 2, rotation: 0 },
        { upload_id: A.uploadId, first_page: 1, last_page: 1, rotation: 180 },
        { upload_id: B.uploadId, first_page: 2, last_page: 2, rotation: 0 },
      ],
      ...COMMON,
    });
  });

  it("select sem cartões aponta o botão de adicionar", () => {
    const draft = parsePdfPress(values({ operation: "select" }), SETTINGS, [A]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-adicionar",
        label: "Páginas escolhidas",
        message: "Adicione ao menos uma página.",
      },
    ]);
  });

  it("intervalos acima do maxItems do schema apontam o botão de adicionar", () => {
    const cards = [card(A, "1, 2, 3"), card(A, "4, 5")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-adicionar",
        label: "Páginas escolhidas",
        message: "Use no máximo 4 intervalos.",
      },
    ]);
  });

  it("fechados acima do teto de saída dão erro antes de enviar", () => {
    const cards = [card(A, "1-200"), card(A, "1-200"), card(A, "1")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-adicionar",
        label: "Páginas escolhidas",
        message: "Escolha no máximo 400 páginas.",
      },
    ]);
  });

  it("intervalos abertos não contam no teto de saída: o fim só o executor conhece", () => {
    const cards = [card(A, "1-200"), card(A, "1-200"), card(A, "10-")];

    expect(parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]).ok).toBe(true);
  });

  it("cartão de um PDF que saiu da lista, ou sem PDF, aponta o campo do arquivo", () => {
    const cards = [card(A, "1"), card(null, "1")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [B]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: "pdf-press-select-0-upload_id",
        label: "PDF do item 1",
        message: "Escolha um dos PDFs enviados.",
      },
      {
        fieldId: "pdf-press-select-1-upload_id",
        label: "PDF do item 2",
        message: "Escolha um dos PDFs enviados.",
      },
    ]);
  });

  it("intervalo inválido aparece no campo Páginas do cartão", () => {
    // O primeiro cartão gera dois intervalos: o erro do segundo cartão continua no índice 1.
    const cards = [card(A, "1, 2"), card(A, "5-2")];

    const draft = parsePdfPress(values({ operation: "select", cards }), SETTINGS, [A]);

    expect(issuesOf(draft)).toEqual([
      {
        fieldId: cardFieldId(1, "pages"),
        label: "Páginas do item 2",
        message: TEXT.pdfPress.ranges.reversed("5-2"),
      },
    ]);
  });

  it.each([
    ["0", TEXT.pdfPress.ranges.startsAtOne],
    ["201", TEXT.pdfPress.ranges.beyondLimit(201, 200)],
    ["", TEXT.pdfPress.ranges.empty],
    ["x", TEXT.pdfPress.ranges.invalidPart("x")],
  ])("cartão com as páginas %j explica o erro no campo", (pages, message) => {
    const draft = parsePdfPress(
      values({ operation: "select", cards: [card(A, pages)] }),
      SETTINGS,
      [A],
    );

    expect(issuesOf(draft)).toEqual([
      { fieldId: "pdf-press-select-0-pages", label: "Páginas do item 1", message },
    ]);
  });
});

describe("pdfPressField", () => {
  it.each([
    [["operation"], "pdf-press-operation", "O que fazer com os PDFs"],
    [["pages_per_document"], "pdf-press-pages_per_document", "Páginas por parte"],
    [["rotation"], "pdf-press-rotation", "Girar as páginas"],
    [["compression"], "pdf-press-compression", "Compressão"],
    [["strip_metadata"], "pdf-press-strip_metadata", "Remover autor e datas das anotações"],
    [["select"], "pdf-press-select-adicionar", "Páginas escolhidas"],
    // Do servidor, o índice é o do intervalo: aponta o grupo, não um cartão.
    [["select", "1", "upload_id"], "pdf-press-select-adicionar", "Páginas escolhidas"],
    [["select", 2, "last_page"], "pdf-press-select-adicionar", "Páginas escolhidas"],
    [["cards", "1", "uploadId"], "pdf-press-select-1-upload_id", "PDF do item 2"],
    [["cards", 2, "pages"], "pdf-press-select-2-pages", "Páginas do item 3"],
    [["cards", 0, "rotation"], "pdf-press-select-0-rotation", "Girar as páginas do item 1"],
  ] as const)("traduz %j", (path, fieldId, label) => {
    expect(pdfPressField(path)).toEqual({ fieldId, label });
  });

  it.each([[["outro"]], [["cards", "x", "uploadId"]], [["cards", "0", "outro"]], [[]]])(
    "fora do formulário %j → null",
    (path) => {
      expect(pdfPressField(path)).toBeNull();
    },
  );
});

describe("pageMapPreview", () => {
  it("merge junta todos os PDFs na ordem", () => {
    expect(pageMapPreview(values(), [A, B], 200)).toEqual([
      { name: "merged.pdf", entries: ["a.pdf, todas as páginas", "b.pdf, todas as páginas"] },
    ]);
  });

  it("merge girado diz o ângulo", () => {
    expect(pageMapPreview(values({ rotation: 90 }), [A], 200)).toEqual([
      { name: "merged.pdf", entries: ["a.pdf, todas as páginas, girada 90°"] },
    ]);
  });

  it("split descreve as partes de cada PDF, no plural", () => {
    expect(
      pageMapPreview(values({ operation: "split", pages_per_document: "2" }), [A], 200),
    ).toEqual([{ name: "a.pdf", entries: ["partes de 2 páginas, na ordem"] }]);
  });

  it.each(["1", "01"])(
    "split com %j página por parte usa o singular, sem zero à esquerda",
    (size) => {
      expect(
        pageMapPreview(values({ operation: "split", pages_per_document: size }), [A], 200),
      ).toEqual([{ name: "a.pdf", entries: ["partes de 1 página, na ordem"] }]);
    },
  );

  it("split com tamanho que não é número mostra o que foi digitado", () => {
    expect(
      pageMapPreview(values({ operation: "split", pages_per_document: "dois" }), [A], 200),
    ).toEqual([{ name: "a.pdf", entries: ["partes de dois páginas, na ordem"] }]);
  });

  it("select descreve página, intervalo, até o fim e rotação de cada cartão", () => {
    const cards = [card(B, "3"), card(A, "1-3, 10-", 270)];

    expect(pageMapPreview(values({ operation: "select", cards }), [A, B], 200)).toEqual([
      {
        name: "selection.pdf",
        entries: [
          "b.pdf, página 3",
          "a.pdf, páginas 1 a 3, girada 270°",
          "a.pdf, da página 10 até o fim, girada 270°",
        ],
      },
    ]);
  });

  it("select mostra como foi digitado o cartão sem PDF ou com páginas que não leem", () => {
    const cards = [card(null, "1"), card(A, "5-2")];

    expect(pageMapPreview(values({ operation: "select", cards }), [A], 200)).toEqual([
      { name: "selection.pdf", entries: ["PDF não escolhido, página 1", "a.pdf, página 5-2"] },
    ]);
  });

  it("sem PDFs nem cartões não há mapa", () => {
    expect(pageMapPreview(values(), [], 200)).toEqual([]);
    expect(pageMapPreview(values({ operation: "select" }), [A], 200)).toEqual([]);
  });
});
