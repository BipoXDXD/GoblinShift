import { describe, expect, it } from "vitest";

import { TEXT } from "../text";
import type { RunPhase, RunStatus } from "./api";
import { announcementFor, type RunProgressSnapshot } from "./announce";

function snapshot(
  status: RunProgressSnapshot["status"],
  phase: RunProgressSnapshot["phase"] = null,
): RunProgressSnapshot {
  return { status, phase };
}

describe("announcementFor", () => {
  it("não anuncia a primeira leitura (a tela já mostra o estado)", () => {
    expect(announcementFor(null, snapshot("running", "processing"))).toBeNull();
  });

  it("não anuncia quando nada mudou", () => {
    expect(
      announcementFor(snapshot("running", "processing"), snapshot("running", "processing")),
    ).toBeNull();
  });

  it("anuncia a mudança de status", () => {
    expect(announcementFor(snapshot("queued"), snapshot("running", "inspecting"))).toBe(
      "Execução em andamento.",
    );
  });

  it("anuncia a mudança de fase dentro do mesmo status", () => {
    expect(
      announcementFor(snapshot("running", "inspecting"), snapshot("running", "packaging")),
    ).toBe("Empacotando os resultados.");
  });

  it("não anuncia fase que sumiu sem mudança de status", () => {
    expect(
      announcementFor(snapshot("running", "finalizing"), snapshot("running", null)),
    ).toBeNull();
  });

  it.each([
    ["succeeded", "Execução concluída."],
    ["succeeded_with_warnings", "Execução concluída com avisos."],
    ["canceled", "Execução cancelada."],
    ["failed", "A execução falhou."],
  ] as const)("anuncia o fim em %s", (status, message) => {
    expect(announcementFor(snapshot("running", "finalizing"), snapshot(status))).toBe(message);
  });

  // A primeira leitura não anuncia; partindo de `queued`, todo outro status é mudança.
  const CHANGED_STATUSES: readonly RunStatus[] = [
    "running",
    "stopping",
    "succeeded",
    "succeeded_with_warnings",
    "canceled",
    "failed",
  ];
  const PHASES: readonly RunPhase[] = [
    "inspecting",
    "processing",
    "packaging",
    "finalizing",
    "retry_wait",
  ];

  it.each(CHANGED_STATUSES)("o texto do status %s vem do TEXT", (status) => {
    expect(announcementFor(snapshot("queued"), snapshot(status))).toBe(
      TEXT.run.announce.statuses[status],
    );
  });

  it.each(PHASES)("o texto da fase %s vem do TEXT", (phase) => {
    expect(announcementFor(snapshot("running", null), snapshot("running", phase))).toBe(
      TEXT.run.announce.phases[phase],
    );
  });

  it("não repete o status que o pedido de cancelamento já anunciou", () => {
    expect(announcementFor(snapshot("queued"), snapshot("canceled"), "canceled")).toBeNull();
  });

  it("anuncia normalmente um status diferente do que o pedido já anunciou", () => {
    expect(announcementFor(snapshot("stopping"), snapshot("canceled"), "stopping")).toBe(
      "Execução cancelada.",
    );
  });
});
