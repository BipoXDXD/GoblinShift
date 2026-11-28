import { describe, expect, it } from "vitest";

import { fakeApi, jsonResponse } from "../../test/fakeApi";
import { getPageMapPreview, type PageMap } from "./api";

const OUTPUT_ID = "7d3c2a1e-0000-4000-8000-0000000000b1";
const route = `GET /api/v1/artifacts/${OUTPUT_ID}/preview`;
const mapHeader = { schema_version: 1, path: "selecao.pdf" };
const numberedPages = Array.from({ length: 401 }, (_, index) => ({
  input_position: 0,
  page_number: index + 1,
}));

function requestPageMap(pages: readonly unknown[]): Promise<PageMap> {
  const api = fakeApi({
    [route]: () =>
      jsonResponse({ ...mapHeader, pages }, 200, { "Content-Type": "application/octet-stream" }),
  });
  return getPageMapPreview(api.client, OUTPUT_ID);
}

describe("prévia do mapa de páginas", () => {
  it("aceita um mapa de 400 páginas", async () => {
    const pages = numberedPages.slice(0, 400);

    await expect(requestPageMap(pages)).resolves.toEqual({ ...mapHeader, pages });
  });

  it("recusa um mapa de 401 páginas", async () => {
    await expect(requestPageMap(numberedPages)).rejects.toMatchObject({
      failure: { kind: "unexpected-response", status: 200 },
    });
  });

  it.each([0, -1])("recusa o número de página %i", async (pageNumber) => {
    const pages = [{ input_position: 0, page_number: pageNumber }];

    await expect(requestPageMap(pages)).rejects.toMatchObject({
      failure: { kind: "unexpected-response", status: 200 },
    });
  });

  it("lê rotation do preview e ignora página sem a chave", async () => {
    const pages = [
      { input_position: 0, page_number: 1, rotation: 90 },
      { input_position: 0, page_number: 2 },
    ];

    await expect(requestPageMap(pages)).resolves.toEqual({ ...mapHeader, pages });
  });

  it.each([0, 45, 360])("recusa a rotação %i, que o produtor nunca escreve", async (rotation) => {
    const pages = [{ input_position: 0, page_number: 1, rotation }];

    await expect(requestPageMap(pages)).rejects.toMatchObject({
      failure: { kind: "unexpected-response", status: 200 },
    });
  });
});
