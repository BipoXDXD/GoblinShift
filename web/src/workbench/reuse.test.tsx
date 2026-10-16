import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
