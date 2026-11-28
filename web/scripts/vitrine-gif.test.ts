import { describe, expect, it } from "vitest";

import { gifArgs } from "./vitrine-gif.ts";

describe("gifArgs", () => {
  it("gifArgs monta um quadro a cada 1,5 s, 960 px e laço infinito", () => {
    const args = gifArgs("/q", "/o/jornada.gif");
    expect(args).toContain("/q/quadro-%02d.png");
    expect(args[args.indexOf("-framerate") + 1]).toBe("2/3");
    expect(args.join(" ")).toMatch(/scale=960:-1.*palettegen.*paletteuse/);
    expect(args.slice(-3)).toEqual(["-loop", "0", "/o/jornada.gif"]);
  });
});
