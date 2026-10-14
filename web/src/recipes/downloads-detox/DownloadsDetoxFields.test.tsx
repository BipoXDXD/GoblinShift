import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import type { RecipeDraft } from "../types";
import { DownloadsDetoxFields } from "./DownloadsDetoxFields";
import type { DownloadsDetoxSettings } from "./schema";

const SETTINGS: DownloadsDetoxSettings = {
  defaults: { deduplicate: true, group_by: "kind", name_mode: "preserve", name_prefix: "foto" },
  limits: { maxPrefixChars: 20 },
};

function setup(errors: ReadonlyMap<string, string> = new Map()) {
  const onDraftChange = vi.fn<(draft: RecipeDraft<"downloads-detox">) => void>();
  const user = userEvent.setup();
  render(
    <DownloadsDetoxFields settings={SETTINGS} errors={errors} onDraftChange={onDraftChange} />,
  );
  const lastDraft = (): RecipeDraft<"downloads-detox"> => {
    const draft = onDraftChange.mock.lastCall?.[0];
    if (draft === undefined) {
      throw new Error("onDraftChange não foi chamado");
    }
    return draft;
  };
  return { user, lastDraft };
}

const prefix = () => screen.queryByRole("textbox", { name: "Prefixo da numeração" });
const nameMode = () => screen.getByRole("combobox", { name: "Nomes dos arquivos" });

describe("DownloadsDetoxFields", () => {
  it("mostra os defaults do catálogo, sem o prefixo enquanto os nomes são preservados", () => {
    setup();

    expect(screen.getByRole("checkbox", { name: "Remover arquivos repetidos" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Organização em pastas" })).toHaveValue("kind");
    expect(nameMode()).toHaveValue("preserve");
    expect(prefix()).toBeNull();
  });

  it("numerar mostra o prefixo com o default e o máximo do catálogo", async () => {
    const { user, lastDraft } = setup();

    await user.selectOptions(nameMode(), "indexed");

    expect(prefix()).toHaveValue("foto");
    expect(prefix()).toHaveAccessibleDescription(/Até 20 caracteres\./);
    expect(lastDraft()).toEqual({
      ok: true,
      command: {
        recipe_id: "downloads-detox",
        recipe_version: 1,
        parameters: {
          deduplicate: true,
          group_by: "kind",
          name_mode: "indexed",
          name_prefix: "foto",
        },
      },
    });
  });

  it("voltar a preservar os nomes tira o prefixo do comando", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(nameMode(), "indexed");
    const field = prefix();
    if (field === null) {
      throw new Error("prefixo ausente");
    }
    await user.clear(field);

    await user.selectOptions(nameMode(), "preserve");
    await user.click(screen.getByRole("checkbox", { name: "Remover arquivos repetidos" }));

    expect(lastDraft()).toMatchObject({
      ok: true,
      command: { parameters: { deduplicate: false, group_by: "kind", name_mode: "preserve" } },
    });
  });

  it("prefixo inválido entrega o erro do campo", async () => {
    const { user, lastDraft } = setup();
    await user.selectOptions(nameMode(), "indexed");
    const field = prefix();
    if (field === null) {
      throw new Error("prefixo ausente");
    }

    await user.clear(field);
    await user.type(field, ".oculto");

    expect(lastDraft()).toEqual({
      ok: false,
      issues: [
        {
          fieldId: "downloads-detox-name_prefix",
          message: "O prefixo não pode começar com ponto.",
        },
      ],
    });
  });

  it("mostra o erro recebido no campo certo", () => {
    setup(new Map([["downloads-detox-group_by", "Opção recusada."]]));

    expect(
      screen.getByRole("combobox", { name: "Organização em pastas" }),
    ).toHaveAccessibleDescription(/Erro: Opção recusada\./);
  });

  it("não tem violações do axe", async () => {
    const { user } = setup(new Map([["downloads-detox-name_prefix", "Prefixo recusado."]]));
    await user.selectOptions(nameMode(), "indexed");

    expect(await axeViolations(document.body)).toEqual([]);
  });
});
