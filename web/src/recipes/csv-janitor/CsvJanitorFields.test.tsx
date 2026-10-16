import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import type { RecipeDraft } from "../types";
import { CsvJanitorFields } from "./CsvJanitorFields";
import type { CsvJanitorSettings } from "./settings";

const MAX_FILTERS = 3;

const SETTINGS: CsvJanitorSettings = {
  defaults: {
    input_delimiter: ";",
    output_delimiter: ",",
    trim_columns: "",
    case_rules: [],
    filters: [],
    deduplicate_by: "",
    output_columns: "",
    formula_guard: "none",
  },
  limits: { maxColumns: 100, maxFilters: MAX_FILTERS },
};

function setup(errors: ReadonlyMap<string, string> = new Map()) {
  const onDraftChange = vi.fn<(draft: RecipeDraft<"csv-janitor">) => void>();
  const user = userEvent.setup();
  render(
    <CsvJanitorFields
      settings={SETTINGS}
      errors={errors}
      onDraftChange={onDraftChange}
      inputs={[]}
    />,
  );
  const lastDraft = (): RecipeDraft<"csv-janitor"> => {
    const draft = onDraftChange.mock.lastCall?.[0];
    if (draft === undefined) {
      throw new Error("onDraftChange não foi chamado");
    }
    return draft;
  };
  return { user, onDraftChange, lastDraft };
}

function parametersOf(draft: RecipeDraft<"csv-janitor">) {
  if (!draft.ok) {
    throw new Error("rascunho inválido");
  }
  return draft.command.parameters;
}

describe("CsvJanitorFields", () => {
  it("mostra os defaults do catálogo", () => {
    setup();

    expect(
      screen.getByRole("combobox", { name: "Delimitador dos arquivos de entrada" }),
    ).toHaveValue(";");
    expect(
      screen.getByRole("combobox", { name: "Células que parecem fórmulas de planilha" }),
    ).toHaveValue("none");
  });

  it("cada mudança entrega o comando atualizado", async () => {
    const { user, lastDraft } = setup();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Delimitador dos arquivos de saída" }),
      ";",
    );
    await user.type(screen.getByRole("textbox", { name: "Colunas finais, na ordem" }), "b{Enter}a");

    const parameters = parametersOf(lastDraft());
    expect(parameters.output_delimiter).toBe(";");
    expect(parameters.output_columns).toEqual(["b", "a"]);
  });

  it("adicionar regra leva o foco à coluna da linha nova", async () => {
    const { user, lastDraft } = setup();

    await user.click(screen.getByRole("button", { name: "Adicionar regra de caixa" }));

    expect(screen.getByRole("textbox", { name: "Coluna (linha 1)" })).toHaveFocus();
    expect(lastDraft().ok).toBe(false);
  });

  it("monta a regra de caixa pelo teclado", async () => {
    const { user, lastDraft } = setup();
    await user.click(screen.getByRole("button", { name: "Adicionar regra de caixa" }));

    await user.keyboard("nome");
    await user.tab();
    await user.selectOptions(screen.getByRole("combobox", { name: "Modo (linha 1)" }), "upper");

    expect(parametersOf(lastDraft()).case_rules).toEqual([{ column: "nome", mode: "upper" }]);
  });

  it("remover a linha devolve o foco ao botão de adicionar", async () => {
    const { user, lastDraft } = setup();
    await user.click(screen.getByRole("button", { name: "Adicionar filtro" }));

    await user.click(screen.getByRole("button", { name: "Remover linha 1" }));

    expect(screen.queryByRole("textbox", { name: "Coluna (linha 1)" })).toBeNull();
    expect(screen.getByRole("button", { name: "Adicionar filtro" })).toHaveFocus();
    expect(parametersOf(lastDraft()).filters).toEqual([]);
  });

  it("filtro 'está vazio' não pede valor", async () => {
    const { user, lastDraft } = setup();
    await user.click(screen.getByRole("button", { name: "Adicionar filtro" }));
    await user.keyboard("status");

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Condição (linha 1)" }),
      "is_empty",
    );

    expect(screen.queryByRole("textbox", { name: "Valor (linha 1)" })).toBeNull();
    expect(parametersOf(lastDraft()).filters).toEqual([
      { column: "status", op: "is_empty", value: null },
    ]);
  });

  it(`não passa do máximo de filtros do catálogo (${String(MAX_FILTERS)})`, async () => {
    const { user } = setup();
    const add = screen.getByRole("button", { name: "Adicionar filtro" });
    for (let index = 0; index < MAX_FILTERS + 1; index += 1) {
      await user.click(add);
    }

    expect(screen.getAllByRole("textbox", { name: /^Coluna \(linha/ })).toHaveLength(MAX_FILTERS);
    expect(add).toHaveAttribute("aria-disabled", "true");
  });

  it("mostra o erro recebido no campo certo", () => {
    setup(new Map([["csv-janitor-output_columns", "Coluna inexistente."]]));

    expect(
      screen.getByRole("textbox", { name: "Colunas finais, na ordem" }),
    ).toHaveAccessibleDescription(/Erro: Coluna inexistente\./);
  });

  it("mostra o erro de uma lista inteira no grupo", () => {
    setup(new Map([["csv-janitor-case_rules-adicionar", "Regras conflitantes."]]));

    expect(screen.getByRole("group", { name: "Regras de caixa" })).toHaveAccessibleDescription(
      /Erro: Regras conflitantes\./,
    );
  });

  it("não tem violações do axe", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Adicionar regra de caixa" }));
    await user.click(screen.getByRole("button", { name: "Adicionar filtro" }));

    expect(await axeViolations(document.body)).toEqual([]);
  });
});
