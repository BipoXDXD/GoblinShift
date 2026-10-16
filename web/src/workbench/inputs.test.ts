import { describe, expect, it } from "vitest";

import type { InputLimits } from "../catalog/inputLimits";
import type { UploadEntry, UploadEntryState } from "../upload/useUploadBatch";
import { orderEntries, runInputs as runInputsWithLimits, uploadAnnouncement } from "./inputs";

const LIMITS: InputLimits = { maxInputs: 5, maxTotalBytes: null, maxFileBytes: null };

function entry(
  localId: string,
  state: UploadEntryState,
  name = `${localId}.csv`,
  content = "x",
): UploadEntry {
  return { localId, file: new File([content], name), state };
}

function runInputs(entries: readonly UploadEntry[]) {
  return runInputsWithLimits(entries, LIMITS);
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
    [5, true],
    [6, false],
  ])("%i arquivos com máximo 5 no catálogo", (count, ok) => {
    const entries = Array.from({ length: count }, (_, index) =>
      entry(String(index), ready(String(index))),
    );

    expect(runInputs(entries).ok).toBe(ok);
  });

  it("acima do máximo, a mensagem traz o máximo do catálogo", () => {
    const entries = Array.from({ length: 3 }, (_, index) =>
      entry(String(index), ready(String(index))),
    );

    expect(
      runInputsWithLimits(entries, { maxInputs: 2, maxTotalBytes: null, maxFileBytes: null }),
    ).toEqual({
      ok: false,
      message: "Use no máximo 2 arquivos.",
    });
  });

  it.each([
    ["no teto", 10, true],
    ["acima do teto", 9, false],
  ])("soma dos tamanhos %s do lote", (_, maxTotalBytes, ok) => {
    const entries = [
      entry("a", ready("1"), "a.zip", "12345"),
      entry("b", ready("2"), "b.zip", "67890"),
      entry("c", { status: "canceled" }, "c.zip", "não conta"),
    ];

    expect(
      runInputsWithLimits(entries, { maxInputs: 5, maxTotalBytes, maxFileBytes: null }).ok,
    ).toBe(ok);
  });

  it("acima do teto de bytes, a mensagem traz o teto", () => {
    const entries = [entry("a", ready("1"), "a.zip", "x".repeat(2_000))];

    expect(
      runInputsWithLimits(entries, { maxInputs: 5, maxTotalBytes: 1_500, maxFileBytes: null }),
    ).toEqual({
      ok: false,
      message: "Os arquivos somam 2 kB; o máximo por execução é 1,5 kB.",
    });
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

describe("runInputs com teto por arquivo", () => {
  const limits = (maxFileBytes: number): InputLimits => ({
    maxInputs: 5,
    maxTotalBytes: null,
    maxFileBytes,
  });

  it.each([
    ["no teto", 5, true],
    ["acima do teto", 4, false],
  ])("arquivo %s é aceito: %s", (_, maxFileBytes, ok) => {
    const entries = [entry("a", ready("1"), "a.mp4", "12345")];

    expect(runInputsWithLimits(entries, limits(maxFileBytes)).ok).toBe(ok);
  });

  it("a mensagem cita o arquivo e o teto; cancelado não conta", () => {
    const entries = [
      entry("a", ready("1"), "a.mp4", "123"),
      entry("b", ready("2"), "grande.mp4", "x".repeat(2_000)),
      entry("c", { status: "canceled" }, "c.mp4", "x".repeat(5_000)),
    ];

    expect(runInputsWithLimits(entries, limits(1_500))).toEqual({
      ok: false,
      message: "grande.mp4 tem 2 kB; o máximo por arquivo nesta receita é 1,5 kB.",
    });
  });
});
