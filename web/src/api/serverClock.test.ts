import { describe, expect, it } from "vitest";

import { createServerClock } from "./serverClock";

const LOCAL_MS = Date.parse("2026-10-14T12:00:00Z");
const HOUR_MS = 60 * 60 * 1_000;

describe("createServerClock", () => {
  it("sem observar, segue o relógio local", () => {
    expect(createServerClock(() => 1_000).now()).toBe(1_000);
  });

  it("segue o Date do servidor 3 h adiante", () => {
    const clock = createServerClock(() => LOCAL_MS);

    clock.observe(new Date(LOCAL_MS + 3 * HOUR_MS).toUTCString());

    expect(clock.now()).toBe(LOCAL_MS + 3 * HOUR_MS);
  });

  it.each([null, "", "amanhã", "2026-10-14T12:00:00Z"])(
    "ignora Date ausente ou inválido (%s)",
    (header) => {
      const clock = createServerClock(() => LOCAL_MS);
      clock.observe(new Date(LOCAL_MS + HOUR_MS).toUTCString());

      clock.observe(header);

      expect(clock.now()).toBe(LOCAL_MS + HOUR_MS);
    },
  );
});
