import { describe, expect, it } from "vitest";
import { z } from "zod";

import mainSource from "./main.tsx?raw";

describe("zodJitless", () => {
  it("desliga a compilação de schemas do Zod", async () => {
    await import("./zodJitless");

    expect(z.config().jitless).toBe(true);
  });

  it("é o primeiro import do main.tsx, antes de qualquer schema ser criado", () => {
    // Os imports de um módulo ES rodam antes do corpo dele: um z.config() no main.tsx chegaria
    // depois dos schemas criados nos módulos importados, e cada um testaria o eval, que a CSP
    // bloqueia.
    expect(/^import .+$/m.exec(mainSource)?.[0]).toBe('import "./zodJitless";');
  });
});
