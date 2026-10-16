import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import type { RecipeDraft, RecipeInput } from "../types";
import { PdfPressFields } from "./PdfPressFields";
import type { PdfPressSettings } from "./schema";

const SETTINGS: PdfPressSettings = {
  defaults: { operation: "merge", pages_per_document: "1", cards: [] },
  limits: { maxPages: 200, maxSelectedPages: 2 },
};
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
const addCard = () => screen.getByRole("button", { name: "Adicionar página" });
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
    expect(parametersOf(lastDraft())).toEqual({ operation: "merge" });
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
    expect(parametersOf(lastDraft())).toEqual({ operation: "split", pages_per_document: 3 });
  });

  it("monta a seleção por cartões numerados, com página repetida", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(operation(), "select");

    await user.click(addCard());
    await user.selectOptions(screen.getByRole("combobox", { name: "PDF da página 1" }), "b.pdf");
    const page = screen.getByRole("textbox", { name: "Número da página 1 no PDF" });
    await user.clear(page);
    await user.type(page, "4");
    await user.click(addCard());

    expect(screen.getByText("1.")).toBeInTheDocument();
    expect(screen.getByText("2.")).toBeInTheDocument();
    expect(parametersOf(lastDraft())).toEqual({
      operation: "select",
      select: [
        { upload_id: B.uploadId, page_number: 4 },
        { upload_id: A.uploadId, page_number: 1 },
      ],
    });
  });

  it("adicionar leva o foco à página do cartão novo", async () => {
    const { user } = setup();
    await user.selectOptions(operation(), "select");

    await user.click(addCard());

    expect(screen.getByRole("textbox", { name: "Número da página 1 no PDF" })).toHaveFocus();
  });

  it("reordena os cartões pelo botão, sem arrastar", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());
    await user.click(addCard());
    await user.selectOptions(screen.getByRole("combobox", { name: "PDF da página 2" }), "b.pdf");

    await user.click(screen.getByRole("button", { name: "Mover para cima página 2 da seleção" }));

    expect(parametersOf(lastDraft())).toEqual({
      operation: "select",
      select: [
        { upload_id: B.uploadId, page_number: 1 },
        { upload_id: A.uploadId, page_number: 1 },
      ],
    });
  });

  it("remover um cartão tira a página e devolve o foco ao botão de adicionar", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    await user.click(screen.getByRole("button", { name: "Remover a página 1" }));

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
        { fieldId: "pdf-press-select-0-upload_id", message: "Escolha um dos PDFs enviados." },
      ],
    });
    expect(screen.getByRole("combobox", { name: "PDF da página 1" })).toHaveDisplayValue(
      "Escolha um PDF",
    );
  });

  it("sem PDFs prontos explica que a seleção espera os arquivos", async () => {
    const { user } = setup([]);

    await user.selectOptions(operation(), "select");

    expect(screen.getByText(/Envie os PDFs primeiro/)).toBeInTheDocument();
  });

  it("mostra o erro do servidor no campo do cartão", async () => {
    const errors = new Map([["pdf-press-select-0-page_number", "Página inválida."]]);
    const { user } = setup([A], errors);
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    expect(
      screen.getByRole("textbox", { name: "Número da página 1 no PDF" }),
    ).toHaveAccessibleDescription(/Página inválida\./);
  });

  it("não tem violações de acessibilidade com cartões", async () => {
    const { user } = setup();
    await user.selectOptions(operation(), "select");
    await user.click(addCard());

    expect(await axeViolations(document.body)).toEqual([]);
  });
});
