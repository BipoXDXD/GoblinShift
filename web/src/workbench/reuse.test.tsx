import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, onTestFinished } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { catalogHandler } from "../test/catalog";
import {
  fakeApi,
  jsonResponse,
  problemResponse,
  runBody,
  RUN_ID,
  screenRequests,
} from "../test/fakeApi";
import { presetBody, PRESET_ID } from "../test/presets";
import { renderRoutes } from "../test/renderRoutes";

const PARAMETERS = {
  input_delimiter: ",",
  output_delimiter: ";",
  trim_columns: ["email"],
  case_rules: [],
  filters: [],
  deduplicate_by: [],
  output_columns: null,
  formula_guard: "tab_prefix",
};

/**
 * O texto de cada região `role="status"` no momento em que ela entrou no DOM, antes de qualquer
 * efeito de um quadro seguinte.
 */
function recordStatusInsertions(): Map<Element, string> {
  const inserted = new Map<Element, string>();
  const observer = new MutationObserver((mutations) => {
    for (const node of mutations.flatMap((mutation) => [...mutation.addedNodes])) {
      if (!(node instanceof Element)) {
        continue;
      }
      const regions = [node, ...node.querySelectorAll("[role=status]")].filter(
        (element) => element.getAttribute("role") === "status",
      );
      for (const region of regions) {
        inserted.set(region, region.textContent ?? "");
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  onTestFinished(() => {
    observer.disconnect();
  });
  return inserted;
}

/** As regiões `role="status"` com o aviso de origem da execução. */
function reuseAnnouncers(): HTMLElement[] {
  return screen
    .getAllByRole("status")
    .filter((region) => region.textContent?.startsWith("Parâmetros da execução de"));
}

function setup(run: () => Response, path = `/bancada/csv-janitor?execucao=${RUN_ID}`) {
  const api = fakeApi({
    "GET /api/v1/catalog": catalogHandler(),
    [`GET /api/v1/runs/${RUN_ID}`]: run,
    [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(presetBody()),
  });
  renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
  return api;
}

describe("Bancada aberta pela execução (reusar configuração)", () => {
  it("pré-preenche com os parâmetros da execução e diz de qual", async () => {
    setup(() => jsonResponse(runBody({ status: "succeeded", parameters: PARAMETERS })));

    expect(await screen.findByText(/^Parâmetros da execução de /)).toBeVisible();
    expect(screen.getByLabelText("Delimitador dos arquivos de saída")).toHaveValue(";");
    expect(screen.getByLabelText("Colunas sem espaços nas pontas")).toHaveValue("email");
  });

  it("parâmetros que já não estão guardados abrem os padrões com o motivo", async () => {
    setup(() => jsonResponse(runBody({ status: "succeeded", parameters: null })));

    expect(await screen.findByText(/não estão mais guardados/)).toBeVisible();
    expect(screen.getByLabelText("Delimitador dos arquivos de saída")).toHaveValue(",");
  });

  it("execução de outra receita abre os padrões e avisa", async () => {
    setup(() =>
      jsonResponse(runBody({ recipe_id: "image-forge", status: "succeeded", parameters: {} })),
    );

    expect(await screen.findByText(/Esta execução é de outra receita/)).toBeVisible();
  });

  it("execução do PDF Press com páginas escolhidas reabre sem elas e pede para escolhê-las de novo", async () => {
    setup(
      () =>
        jsonResponse(
          runBody({
            recipe_id: "pdf-press",
            status: "succeeded",
            parameters: {
              operation: "select",
              pages_per_document: 1,
              select: [{ upload_id: "0b9f6c1e-3a2d-4e5f-8a7b-6c5d4e3f2a1b", page_number: 2 }],
            },
          }),
        ),
      `/bancada/pdf-press?execucao=${RUN_ID}`,
    );

    const notice = await screen.findByText(/^Parâmetros da execução de /);
    expect(notice).toHaveTextContent(
      "As páginas escolhidas dependem dos arquivos enviados; escolha-as de novo.",
    );
    expect(screen.queryByText(/Esta execução é de outra receita/)).toBeNull();
    expect(screen.getByRole("combobox", { name: "O que fazer com os PDFs" })).toHaveValue("select");
  });

  it("com a execução já em cache, a região viva nasce vazia e só depois recebe o aviso", async () => {
    const run = runBody({ status: "succeeded", parameters: PARAMETERS });
    const api = fakeApi({
      "GET /api/v1/catalog": catalogHandler(),
      "GET /api/v1/runs": () => jsonResponse({ data: [run], next_cursor: null }),
      [`GET /api/v1/runs/${RUN_ID}`]: () => jsonResponse(run),
    });
    const { router } = renderRoutes({
      routes: appRoutes(api.client),
      path: "/historico",
      client: api.client,
    });
    const user = userEvent.setup();
    const reuse = async (): Promise<void> => {
      await user.click(await screen.findByRole("link", { name: /^Reusar configuração/ }));
      // O aviso aparece duas vezes: no parágrafo visível e na região viva.
      await screen.findAllByText(/^Parâmetros da execução de /);
    };
    await reuse();
    await router.navigate("/historico");
    // Na volta, catálogo e execução vêm do cache: a Bancada monta já com o aviso pronto. O clique é
    // um evento discreto, e o React roda os efeitos dele sem esperar o navegador pintar.
    const inserted = recordStatusInsertions();

    await reuse();

    await waitFor(() => {
      expect(reuseAnnouncers()).toHaveLength(1);
    });
    // Montada já com o texto, a região não seria anunciada (WCAG 4.1.3).
    expect(inserted.get(reuseAnnouncers()[0] ?? document.body)).toBe("");
  });

  it("execução que não existe mostra a falha e a Bancada com os padrões", async () => {
    setup(() => problemResponse(404, "NOT_FOUND"));

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.getByLabelText("Escolher arquivos")).toBeInTheDocument();
  });

  it("com ?preset= e ?execucao= juntos, vale o preset e a execução nem é pedida", async () => {
    const api = setup(
      () => jsonResponse(runBody({ status: "succeeded", parameters: PARAMETERS })),
      `/bancada/csv-janitor?execucao=${RUN_ID}&preset=${PRESET_ID}`,
    );

    expect(await screen.findByText("Parâmetros do preset Clientes do mês.")).toBeVisible();
    expect(screen.getByLabelText("Colunas sem espaços nas pontas")).toHaveValue("nome");
    expect(
      screenRequests(api.requests).filter((request) => request.url.includes("/runs/")),
    ).toEqual([]);
  });
});
