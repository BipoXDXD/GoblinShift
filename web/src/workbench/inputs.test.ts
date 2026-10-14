import { describe, expect, it } from "vitest";

import type { UploadEntry, UploadEntryState } from "../upload/useUploadBatch";
import { MAX_RUN_INPUTS, orderEntries, runInputs, uploadAnnouncement } from "./inputs";

function entry(localId: string, state: UploadEntryState, name = `${localId}.csv`): UploadEntry {
  return { localId, file: new File(["x"], name), state };
}

const ready = (uploadId: string): UploadEntryState => ({ status: "ready", uploadId, sha256: "s" });

describe("orderEntries", () => {
  it("segue a ordem escolhida e põe os novos no fim, na ordem de chegada", () => {
    const entries = [entry("a", ready("1")), entry("b", ready("2")), entry("c", ready("3"))];

    expect(orderEntries(entries, ["c", "a"]).map((item) => item.localId)).toEqual(["c", "a", "b"]);
  });

  it("ignora ids que já saíram do lote", () => {
    const entries = [entry("a", ready("1"))];

    expect(orderEntries(entries, ["x", "a"]).map((item) => item.localId)).toEqual(["a"]);
  });

  it("lote vazio", () => {
    expect(orderEntries([], ["a"])).toEqual([]);
  });
});

describe("runInputs", () => {
  it("devolve os ids dos uploads na ordem da lista", () => {
    expect(runInputs([entry("b", ready("2")), entry("a", ready("1"))])).toEqual({
      ok: true,
      uploadIds: ["2", "1"],
    });
  });

  it("ignora os cancelados", () => {
    expect(runInputs([entry("a", ready("1")), entry("b", { status: "canceled" })])).toEqual({
      ok: true,
      uploadIds: ["1"],
    });
  });

  it.each([
    ["sem arquivos", [], "Escolha pelo menos um arquivo."],
    ["só cancelados", [entry("a", { status: "canceled" })], "Escolha pelo menos um arquivo."],
    [
      "envio em curso",
      [entry("a", ready("1")), entry("b", { status: "sending", loadedBytes: 1, totalBytes: 2 })],
      "Espere o envio dos arquivos terminar.",
    ],
    ["na fila", [entry("a", { status: "waiting" })], "Espere o envio dos arquivos terminar."],
    [
      "confirmando",
      [entry("a", { status: "confirming" })],
      "Espere o envio dos arquivos terminar.",
    ],
    [
      "falha",
      [entry("a", { status: "failed", failure: { kind: "network" } })],
      "Remova ou envie de novo os arquivos que falharam.",
    ],
  ])("recusa %s", (_label, entries, message) => {
    expect(runInputs(entries)).toEqual({ ok: false, message });
  });

  it.each([
    [MAX_RUN_INPUTS, true],
    [MAX_RUN_INPUTS + 1, false],
  ])("%i arquivos", (count, ok) => {
    const entries = Array.from({ length: count }, (_, index) =>
      entry(String(index), ready(String(index))),
    );

    expect(runInputs(entries).ok).toBe(ok);
  });
});

describe("uploadAnnouncement", () => {
  it("nada a anunciar sem mudança de estado", () => {
    const entries = [entry("a", { status: "sending", loadedBytes: 1, totalBytes: 9 })];

    expect(uploadAnnouncement(new Map([["a", "sending"]]), entries)).toBeNull();
  });

  it("anuncia o arquivo que ficou pronto ou falhou, não o progresso", () => {
    const entries = [
      entry("a", ready("1"), "clientes.csv"),
      entry("b", { status: "failed", failure: { kind: "network" } }, "vendas.csv"),
      entry("c", { status: "sending", loadedBytes: 1, totalBytes: 9 }),
    ];
    const previous = new Map([
      ["a", "confirming"],
      ["b", "sending"],
      ["c", "waiting"],
    ] as const);

    expect(uploadAnnouncement(previous, entries)).toBe(
      "clientes.csv enviado. Falha ao enviar vendas.csv.",
    );
  });
});
