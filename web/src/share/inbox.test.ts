import { beforeEach, describe, expect, it } from "vitest";

import { fakeCacheStorage, useBlobResponse } from "../test/shareInbox";
import {
  peekSharedNames,
  SHARE_CACHE,
  SHARE_FIELD,
  SHARE_TTL_MS,
  purgeExpiredShares,
  ShareRejected,
  storeShared,
  takeShared,
} from "./inbox";

const NOW = 1_800_000_000_000;

function formWith(...files: File[]): FormData {
  const form = new FormData();
  for (const file of files) {
    form.append(SHARE_FIELD, file);
  }
  return form;
}

const MIB = 1024 * 1024;

async function storedPaths(caches: CacheStorage): Promise<string[]> {
  const keys = await (await caches.open(SHARE_CACHE)).keys();
  return keys.map((request) => new URL(request.url).pathname);
}

async function textOf(file: File): Promise<string> {
  return file.text();
}

describe("caixa de entrada do compartilhamento", () => {
  beforeEach(() => {
    useBlobResponse();
  });

  it("guarda e devolve os arquivos com nome e tipo, uma vez só", async () => {
    const caches = fakeCacheStorage();
    const form = formWith(
      new File(["a,b\n1,2\n"], "tabela.csv", { type: "text/csv" }),
      new File(["%PDF-1.7"], "nota.pdf", { type: "application/pdf" }),
    );

    const id = await storeShared(caches, form, { now: NOW, newId: () => "abc" });
    const files = await takeShared(caches, id, NOW + 1_000);

    expect(id).toBe("abc");
    expect(files).not.toBe("expired");
    const [csv, pdf] = files === "expired" ? [] : files;
    expect(csv?.name).toBe("tabela.csv");
    expect(csv?.type).toBe("text/csv");
    expect(await textOf(csv ?? new File([], ""))).toBe("a,b\n1,2\n");
    expect(pdf?.name).toBe("nota.pdf");
    expect(pdf?.type).toBe("application/pdf");
    expect(await takeShared(caches, id, NOW + 2_000)).toBe("expired");
  });

  it("entrada com mais de 1 h volta como expired e é apagada", async () => {
    const caches = fakeCacheStorage();
    const id = await storeShared(caches, formWith(new File(["x"], "a.csv")), {
      now: NOW,
      newId: () => "velho",
    });

    expect(await peekSharedNames(caches, id, NOW + SHARE_TTL_MS)).toEqual(["a.csv"]);
    expect(await takeShared(caches, id, NOW + SHARE_TTL_MS + 1)).toBe("expired");
    expect(await (await caches.open(SHARE_CACHE)).keys()).toHaveLength(0);
  });

  it("um compartilhamento novo apaga os vencidos de antes", async () => {
    const caches = fakeCacheStorage();
    await storeShared(caches, formWith(new File(["x"], "a.csv")), {
      now: NOW,
      newId: () => "velho",
    });

    await storeShared(caches, formWith(new File(["y"], "b.csv")), {
      now: NOW + SHARE_TTL_MS + 1,
      newId: () => "novo",
    });

    const keys = await (await caches.open(SHARE_CACHE)).keys();
    expect(keys.map((request) => new URL(request.url).pathname)).toEqual([
      "/__compartilhado/novo/0",
    ]);
  });

  it("nome com acento e barra sobrevive", async () => {
    const caches = fakeCacheStorage();
    const id = await storeShared(
      caches,
      formWith(new File(["x"], "relatório/1.csv", { type: "text/csv" })),
      { now: NOW, newId: () => "acento" },
    );

    expect(await peekSharedNames(caches, id, NOW)).toEqual(["relatório/1.csv"]);
    const files = await takeShared(caches, id, NOW);
    expect(files === "expired" ? files : files.map((file) => file.name)).toEqual([
      "relatório/1.csv",
    ]);
  });

  it("espiar os nomes não consome a entrada", async () => {
    const caches = fakeCacheStorage();
    const id = await storeShared(caches, formWith(new File(["x"], "a.csv")), {
      now: NOW,
      newId: () => "espiar",
    });

    await peekSharedNames(caches, id, NOW);

    expect(await takeShared(caches, id, NOW)).not.toBe("expired");
  });

  it("id desconhecido é expired", async () => {
    const caches = fakeCacheStorage();

    expect(await peekSharedNames(caches, "nada", NOW)).toBe("expired");
    expect(await takeShared(caches, "nada", NOW)).toBe("expired");
  });

  it("mantém a ordem dos arquivos com mais de dez entradas", async () => {
    const caches = fakeCacheStorage();
    const names = Array.from({ length: 12 }, (_, index) => `f${String(index)}.csv`);
    const id = await storeShared(caches, formWith(...names.map((name) => new File(["x"], name))), {
      now: NOW,
      newId: () => "ordem",
    });

    expect(await peekSharedNames(caches, id, NOW)).toEqual(names);
  });

  it("um vencido some na leitura, sem precisar de outro compartilhamento", async () => {
    const caches = fakeCacheStorage();
    await storeShared(caches, formWith(new File(["x"], "a.csv")), { now: NOW, newId: () => "a" });
    await storeShared(caches, formWith(new File(["y"], "b.csv")), {
      now: NOW + SHARE_TTL_MS / 2,
      newId: () => "b",
    });

    await peekSharedNames(caches, "b", NOW + SHARE_TTL_MS + 1);

    expect(await storedPaths(caches)).toEqual(["/__compartilhado/b/0"]);
  });

  it("tirar um compartilhamento também apaga os vencidos de outros", async () => {
    const caches = fakeCacheStorage();
    await storeShared(caches, formWith(new File(["x"], "a.csv")), { now: NOW, newId: () => "a" });
    await storeShared(caches, formWith(new File(["y"], "b.csv")), {
      now: NOW + SHARE_TTL_MS / 2,
      newId: () => "b",
    });

    await takeShared(caches, "b", NOW + SHARE_TTL_MS + 1);

    expect(await storedPaths(caches)).toEqual([]);
  });

  it("a limpeza do service worker apaga só os vencidos", async () => {
    const caches = fakeCacheStorage();
    await storeShared(caches, formWith(new File(["x"], "a.csv")), { now: NOW, newId: () => "a" });
    await storeShared(caches, formWith(new File(["y"], "b.csv")), {
      now: NOW + SHARE_TTL_MS / 2,
      newId: () => "b",
    });

    await purgeExpiredShares(caches, NOW + SHARE_TTL_MS + 1);

    expect(await storedPaths(caches)).toEqual(["/__compartilhado/b/0"]);
  });

  it("falha num put apaga o compartilhamento inteiro e propaga o erro", async () => {
    const caches = fakeCacheStorage({ failOnPut: 2 });
    const form = formWith(
      new File(["1"], "a.csv"),
      new File(["2"], "b.csv"),
      new File(["3"], "c.csv"),
    );

    await expect(storeShared(caches, form, { now: NOW, newId: () => "cota" })).rejects.toThrow(
      "cota cheia",
    );
    expect(await storedPaths(caches)).toEqual([]);
  });

  it("recusa arquivo comum acima de 50 MiB sem gravar nada", async () => {
    const caches = fakeCacheStorage();
    const form = formWith(
      new File(["x"], "a.csv"),
      new File([new Uint8Array(50 * MIB + 1)], "grande.pdf"),
    );

    await expect(storeShared(caches, form, { now: NOW, newId: () => "teto" })).rejects.toThrow(
      ShareRejected,
    );
    expect(await storedPaths(caches)).toEqual([]);
  });

  it("vídeo acima de 50 MiB passa, porque o teto dele é 256 MiB", async () => {
    const caches = fakeCacheStorage();
    const form = formWith(new File([new Uint8Array(50 * MIB + 1)], "clipe.mp4"));

    await storeShared(caches, form, { now: NOW, newId: () => "video" });

    expect(await storedPaths(caches)).toEqual(["/__compartilhado/video/0"]);
  });

  it("recusa mais de 100 arquivos num compartilhamento", async () => {
    const caches = fakeCacheStorage();
    const files = Array.from(
      { length: 101 },
      (_, index) => new File(["x"], `f${String(index)}.csv`),
    );

    await expect(
      storeShared(caches, formWith(...files), { now: NOW, newId: () => "muitos" }),
    ).rejects.toThrow(ShareRejected);
    expect(await storedPaths(caches)).toEqual([]);
  });
});
