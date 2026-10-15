import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  GOBLIN_STATES,
  goblinOf,
  GoblinIllustration,
  type GoblinState,
} from "./GoblinIllustration";

describe("goblinOf", () => {
  it("cada estado tem a sua goblin, sem repetir", () => {
    const names = GOBLIN_STATES.map((state) => goblinOf(state).name);

    expect(names).toEqual(["Lua", "Ada", "Nyx", "Ferrugem"]);
  });
});

describe("GoblinIllustration", () => {
  it.each<[GoblinState, RegExp]>([
    ["vazio", /^Lua, /],
    ["carregando", /^Ada, /],
    ["erro", /^Nyx, /],
    ["sucesso", /^Ferrugem, /],
  ])("no estado %s mostra a goblin com texto alternativo", (state, alt) => {
    render(<GoblinIllustration state={state} />);

    expect(screen.getByRole("img")).toHaveAccessibleName(alt);
  });

  it("reserva o tamanho, carrega tarde e oferece AVIF antes do WebP em 1× e 2×", () => {
    const { container } = render(<GoblinIllustration state="vazio" />);

    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("width", "128");
    expect(img).toHaveAttribute("height", "160");
    expect(img).toHaveAttribute("loading", "lazy");
    expect(img).toHaveAttribute("decoding", "async");
    const sources = [...container.querySelectorAll("source")];
    expect(sources.map((source) => source.getAttribute("type"))).toEqual([
      "image/avif",
      "image/webp",
    ]);
    for (const source of sources) {
      expect(source.getAttribute("srcset")).toMatch(/lua-128w\.\w+ 1x, .*lua-256w\.\w+ 2x$/);
    }
  });
});
