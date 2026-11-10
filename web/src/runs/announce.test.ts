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

  it.each(Object.entries(TEXT.run.announce.statuses))(
    "o texto do status %s vem do TEXT",
    (status, message) => {
      expect(announcementFor(snapshot("queued"), snapshot(status as RunStatus))).toBe(
        status === "queued" ? null : message,
      );
    },
  );

  it.each(Object.entries(TEXT.run.announce.phases))(
    "o texto da fase %s vem do TEXT",
    (phase, message) => {
      expect(
        announcementFor(snapshot("running", null), snapshot("running", phase as RunPhase)),
      ).toBe(message);
    },
  );
});
