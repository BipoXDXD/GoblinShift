import { beforeEach, describe, expect, it } from "vitest";

import { fakeCacheStorage, useBlobResponse } from "../test/shareInbox";
import { SHARE_FIELD } from "./inbox";
import { isShareRequest, receiveShare } from "./receive";

const ORIGIN = "https://goblinshift.test";

describe("filtro do service worker", () => {
  it.each([
    ["POST", `${ORIGIN}/compartilhar`, true],
    ["GET", `${ORIGIN}/compartilhar`, false],
    ["POST", "https://outro.test/compartilhar", false],
    ["POST", `${ORIGIN}/compartilhar/x`, false],
    ["POST", `${ORIGIN}/api/v1/uploads`, false],
  ])("%s %s → %s", (method, url, expected) => {
    expect(isShareRequest({ method, url }, ORIGIN)).toBe(expected);
  });
});

describe("recebimento do compartilhamento", () => {
  beforeEach(() => {
    useBlobResponse();
  });

  it("grava e manda para a página do compartilhamento com o id", async () => {
    const form = new FormData();
    form.append(SHARE_FIELD, new File(["x"], "a.csv"));

    const location = await receiveShare(
      { formData: () => Promise.resolve(form) },
      { caches: fakeCacheStorage(), now: 0, newId: () => "abc", origin: ORIGIN },
    );

    expect(location).toBe(`${ORIGIN}/compartilhar?id=abc`);
  });

  it("corpo ilegível manda para a Oficina com o aviso", async () => {
    const location = await receiveShare(
      { formData: () => Promise.reject(new TypeError("corpo inválido")) },
      { caches: fakeCacheStorage(), now: 0, newId: () => "abc", origin: ORIGIN },
    );

    expect(location).toBe(`${ORIGIN}/?compartilhamento=indisponivel`);
  });

  it("cota cheia manda para a Oficina com o aviso", async () => {
    const form = new FormData();
    form.append(SHARE_FIELD, new File(["x"], "a.csv"));

    const location = await receiveShare(
      { formData: () => Promise.resolve(form) },
      { caches: fakeCacheStorage({ failOnPut: 1 }), now: 0, newId: () => "abc", origin: ORIGIN },
    );

    expect(location).toBe(`${ORIGIN}/?compartilhamento=indisponivel`);
  });
});
