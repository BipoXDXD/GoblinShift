import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, screenRequests } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

const GUIDE_PATH = "/acesso/guia-da-api";

function renderGuide() {
  const api = fakeApi({});
  const view = renderRoutes({
    routes: appRoutes(api.client),
    path: GUIDE_PATH,
    client: api.client,
  });
  return { ...view, api };
}

/** O comando do passo: o `<code>` dentro do bloco rolável, não os trechos do texto. */
function commandOf(step: HTMLElement): string {
  return within(within(step).getByRole("region")).getByRole("code").textContent;
}

async function steps(): Promise<HTMLElement[]> {
  const list = await screen.findByRole("list", { name: "Passo a passo" });
  return within(list).getAllByRole("listitem");
}

describe("Guia da API", () => {
  it("abre com o foco no título e não consulta a API da tela", async () => {
    const { api } = renderGuide();

    await expectHeadingFocused("Usar a API com chave");
    expect(screenRequests(api.requests)).toEqual([]);
  });

  it("aponta a documentação interativa e o contrato como páginas do servidor", async () => {
    renderGuide();

    expect(await screen.findByRole("link", { name: /documentação interativa/i })).toHaveAttribute(
      "href",
      "/docs",
    );
    expect(screen.getByRole("link", { name: /contrato OpenAPI/i })).toHaveAttribute(
      "href",
      "/openapi.json",
    );
  });

  it("leva ao Acesso para criar a chave", async () => {
    renderGuide();

    expect(await screen.findByRole("link", { name: "Criar uma chave no Acesso" })).toHaveAttribute(
      "href",
      "/acesso",
    );
  });

  it("explica os quatro escopos", async () => {
    renderGuide();

    const table = await screen.findByRole("table", { name: "Escopos da chave" });
    const scopes = within(table)
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent);
    expect(scopes).toEqual(["files:write", "files:read", "runs:write", "runs:read"]);
  });

  it("o passo a passo vai do upload ao download, na ordem da API", async () => {
    renderGuide();

    const commands = (await steps()).map(commandOf);
    const routes = commands.map((command) => /\/api\/v1\/([^"\s]+)/.exec(command)?.[1]);
    expect(routes).toEqual([
      "uploads",
      "uploads/$UPLOAD_ID/content",
      "runs",
      "runs/$RUN_ID",
      "runs/$RUN_ID/results",
      "artifacts/$ARTIFACT_ID/download",
    ]);
  });

  it("os comandos usam a origem atual e a chave de uma variável, nunca uma chave escrita", async () => {
    renderGuide();

    for (const step of await steps()) {
      const command = commandOf(step);
      expect(command).toContain(`${window.location.origin}/api/v1/`);
      expect(command).toContain('Authorization: Bearer $GS_API_KEY"');
      expect(command).not.toMatch(/gsk_[A-Za-z0-9]/);
    }
  });

  it("criar a execução leva a Idempotency-Key guardada numa variável", async () => {
    renderGuide();

    const [, , createRun] = await steps();
    if (createRun === undefined) throw new Error("sem o passo de criar a execução");
    const command = commandOf(createRun);
    expect(command).toContain('Idempotency-Key: $IDEMPOTENCY_KEY"');
    expect(createRun).toHaveTextContent("repita a mesma chave");
  });

  it("os blocos de código rolam pelo teclado e têm nome", async () => {
    renderGuide();

    for (const step of await steps()) {
      const region = within(step).getByRole("region");
      expect(region).toHaveAttribute("tabindex", "0");
      expect(region).toHaveAccessibleName();
    }
  });

  it("não tem violações do axe", async () => {
    const { container } = renderGuide();
    await steps();

    expect(await axeViolations(container)).toEqual([]);
  });
});
