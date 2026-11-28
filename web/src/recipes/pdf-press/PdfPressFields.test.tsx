import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import type { RecipeDraft, RecipeInput } from "../types";
import { PdfPressFields } from "./PdfPressFields";
import type { PdfPressSettings } from "./schema";

const SETTINGS: PdfPressSettings = {
  defaults: {
    operation: "merge",
    pages_per_document: "1",
    rotation: 0,
    compression: "none",
    strip_metadata: true,
    cards: [],
  },
  limits: { maxPages: 200, maxRanges: 2, maxOutputPages: 400 },
};
const COMMON = { compression: "none", strip_metadata: true } as const;
const A: RecipeInput = { uploadId: "4eb3e442-3f30-4d83-8d34-cb18a99a46cb", name: "a.pdf" };
const B: RecipeInput = { uploadId: "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2", name: "b.pdf" };

function setup(
  inputs: readonly RecipeInput[] = [A, B],
  errors: ReadonlyMap<string, string> = new Map(),
) {
  const onDraftChange = vi.fn<(draft: RecipeDraft<"pdf-press">) => void>();
  const user = userEvent.setup();
  const view = render(
    <PdfPressFields
      settings={SETTINGS}
      errors={errors}
      onDraftChange={onDraftChange}
      inputs={inputs}
    />,
  );
  const lastDraft = (): RecipeDraft<"pdf-press"> => {
    const draft = onDraftChange.mock.lastCall?.[0];
    if (draft === undefined) {
      throw new Error("onDraftChange não foi chamado");
    }
    return draft;
  };
  const rerender = (next: readonly RecipeInput[]): void => {
    view.rerender(
      <PdfPressFields
        settings={SETTINGS}
        errors={errors}
        onDraftChange={onDraftChange}
        inputs={next}
      />,
    );
  };
  return { user, lastDraft, rerender };
}

const operation = () => screen.getByRole("combobox", { name: "O que fazer com os PDFs" });
const addCard = () => screen.getByRole("button", { name: "Adicionar item" });
const pagesOf = (position: number) =>
  screen.getByRole("textbox", { name: `Páginas do item ${String(position)}` });
const preview = () => screen.getByRole("region", { name: "Mapa de páginas previsto" });

function parametersOf(draft: RecipeDraft<"pdf-press">) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

describe("PdfPressFields", () => {
  it("começa juntando os PDFs, com o mapa na ordem da lista", () => {
    const { lastDraft } = setup();

    expect(operation()).toHaveValue("merge");
    expect(parametersOf(lastDraft())).toEqual({ operation: "merge", rotation: 0, ...COMMON });
    expect(
      within(preview())
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "merged.pdfa.pdf, todas as páginasb.pdf, todas as páginas",
      "a.pdf, todas as páginas",
      "b.pdf, todas as páginas",
    ]);
  });

  it("separar mostra o tamanho das partes com o máximo do catálogo", async () => {
    const { user, lastDraft } = setup();

    await user.selectOptions(operation(), "split");
    const size = screen.getByRole("textbox", { name: "Páginas por parte" });
    await user.clear(size);
    await user.type(size, "3");

    expect(size).toHaveAccessibleDescription(/De 1 a 200\./);
    expect(parametersOf(lastDraft())).toEqual({
      operation: "split",
      pages_per_document: 3,
      rotation: 0,
      ...COMMON,
    });
  });

  it("monta a seleção por cartões numerados, com intervalos e rotação por cartão", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(operation(), "select");

    await user.click(addCard());
    await user.selectOptions(screen.getByRole("combobox", { name: "PDF do item 1" }), "b.pdf");
    await user.clear(pagesOf(1));
    await user.type(pagesOf(1), "4-6");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Girar as páginas do item 1" }),
      "90° no sentido horário",
    );
    await user.click(addCard());

    expect(screen.getByText("1.")).toBeInTheDocument();
    expect(screen.getByText("2.")).toBeInTheDocument();
    expect(parametersOf(lastDraft())).toEqual({
      operation: "select",
      select: [
        { upload_id: B.uploadId, first_page: 4, last_page: 6, rotation: 90 },
        { upload_id: A.uploadId, first_page: 1, last_page: 1, rotation: 0 },
      ],
      ...COMMON,
    });
  });

  it("a rotação global aparece no merge e no split, e não no select", async () => {
    const { user, lastDraft } = setup();
    const rotation = () => screen.queryByRole("combobox", { name: "Girar as páginas" });

    await user.selectOptions(screen.getByRole("combobox", { name: "Girar as páginas" }), "180°");
    expect(parametersOf(lastDraft())).toMatchObject({ operation: "merge", rotation: 180 });

    await user.selectOptions(operation(), "select");
    expect(rotation()).toBeNull();
  });

  it("compressão e strip_metadata valem em todas as operações", async () => {
    const { user, lastDraft } = setup([A]);
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Compressão" }),
      "Sem perdas: reorganiza o arquivo e recomprime o que der, sem mexer na qualidade das imagens",
    );
    await user.click(screen.getByRole("checkbox", { name: "Remover autor e datas das anotações" }));

    expect(parametersOf(lastDraft())).toMatchObject({
      compression: "lossless",
      strip_metadata: false,
    });
    expect(
      screen.getByRole("checkbox", { name: "Remover autor e datas das anotações" }),
    ).toHaveAccessibleDescription(
      "Título, autor e programa do documento original nunca passam para o PDF gerado.",
    );
  });

  it("o campo Páginas descreve o formato e o erro com aria-describedby", async () => {
    const errors = new Map([["pdf-press-select-0-pages", "As páginas começam em 1."]]);
    const { user } = setup([A], errors);
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    expect(pagesOf(1)).toHaveAccessibleDescription(
      /Como 1-3, 7, 10- \(do 10 até o fim\).*As páginas começam em 1\./,
    );
    expect(pagesOf(1)).toHaveAttribute("aria-invalid", "true");
    expect(await axeViolations(document.body)).toEqual([]);
  });

  it("o mapa previsto descreve intervalos e rotação", async () => {
    const { user } = setup([A]);
    await user.selectOptions(operation(), "select");
    await user.click(addCard());
    await user.clear(pagesOf(1));
    await user.type(pagesOf(1), "1-3, 10-");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Girar as páginas do item 1" }),
      "180°",
    );

    expect(within(preview()).getByText("a.pdf, páginas 1 a 3, girada 180°")).toBeInTheDocument();
    expect(
      within(preview()).getByText("a.pdf, da página 10 até o fim, girada 180°"),
    ).toBeInTheDocument();
  });

  it("adicionar leva o foco à página do cartão novo", async () => {
    const { user } = setup();
    await user.selectOptions(operation(), "select");

    await user.click(addCard());

    expect(pagesOf(1)).toHaveFocus();
  });

  it("reordena os cartões pelo botão, sem arrastar", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());
    await user.click(addCard());
    await user.selectOptions(screen.getByRole("combobox", { name: "PDF do item 2" }), "b.pdf");

    await user.click(screen.getByRole("button", { name: "Mover para cima item 2 da seleção" }));

    expect(parametersOf(lastDraft())).toEqual({
      operation: "select",
      select: [
        { upload_id: B.uploadId, first_page: 1, last_page: 1, rotation: 0 },
        { upload_id: A.uploadId, first_page: 1, last_page: 1, rotation: 0 },
      ],
      ...COMMON,
    });
  });

  it("remover um cartão tira a página e devolve o foco ao botão de adicionar", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    await user.click(screen.getByRole("button", { name: "Remover o item 1" }));

    expect(addCard()).toHaveFocus();
    expect(lastDraft()).toMatchObject({ ok: false });
  });

  it("o botão de adicionar avisa, sem sumir, quando a seleção enche", async () => {
    const { user } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());
    await user.click(addCard());

    await user.click(addCard());

    expect(addCard()).toHaveAttribute("aria-disabled", "true");
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
  });

  it("um PDF que sai da lista invalida o cartão que o citava", async () => {
    const { user, lastDraft, rerender } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    rerender([B]);

    expect(lastDraft()).toEqual({
      ok: false,
      issues: [
        {
          fieldId: "pdf-press-select-0-upload_id",
          label: "PDF do item 1",
          message: "Escolha um dos PDFs enviados.",
        },
      ],
    });
    expect(screen.getByRole("combobox", { name: "PDF do item 1" })).toHaveDisplayValue(
      "Escolha um PDF",
    );
  });

  it("sem PDFs prontos explica que a seleção espera os arquivos", async () => {
    const { user } = setup([]);

    await user.selectOptions(operation(), "select");

    expect(screen.getByText(/Envie os PDFs primeiro/)).toBeInTheDocument();
  });

  it("mostra o erro do servidor no campo do cartão", async () => {
    const errors = new Map([["pdf-press-select-0-upload_id", "Escolha um dos PDFs enviados."]]);
    const { user } = setup([A], errors);
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    expect(screen.getByRole("combobox", { name: "PDF do item 1" })).toHaveAccessibleDescription(
      /Escolha um dos PDFs enviados\./,
    );
  });

  it("não tem violações de acessibilidade com cartões", async () => {
    const { user } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    expect(await axeViolations(document.body)).toEqual([]);
  });
});
