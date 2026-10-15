import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  GOBLIN_STATES,
  goblinOf,
  GoblinIllustration,
  goblinsOf,
  type GoblinState,
} from "./GoblinIllustration";

const KEYS = Array.from({ length: 200 }, (_, index) => `chave-${String(index)}`);

describe("goblinsOf", () => {
  it("cada estado tem as suas goblins, e as seis aparecem uma vez só", () => {
    const names = GOBLIN_STATES.map((state) => goblinsOf(state).map((goblin) => goblin.name));

    expect(names).toEqual([["Lua", "Jade"], ["Ada", "Fagulha"], ["Nyx"], ["Ferrugem"]]);
  });
});

describe("goblinOf (rotação estável)", () => {
  it("a mesma chave dá sempre a mesma goblin", () => {
    for (const key of KEYS) {
      expect(goblinOf("vazio", key).name).toBe(goblinOf("vazio", key).name);
    }
  });

  it.each<[GoblinState, string[]]>([
    ["vazio", ["Jade", "Lua"]],
    ["carregando", ["Ada", "Fagulha"]],
  ])("no estado %s, chaves diferentes alternam entre %j", (state, expected) => {
    const counts = new Map<string, number>();
    for (const key of KEYS) {
      const { name } = goblinOf(state, key);
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }

    expect([...counts.keys()].sort()).toEqual(expected);
    // Nenhuma das duas fica com menos de um terço: a rotação não pende para um lado.
    for (const count of counts.values()) {
      expect(count).toBeGreaterThan(KEYS.length / 3);
    }
  });

  it("estado com uma goblin só ignora a chave", () => {
    expect(new Set(KEYS.map((key) => goblinOf("erro", key).name))).toEqual(new Set(["Nyx"]));
  });

  it("chave vazia ainda escolhe uma goblin do estado", () => {
    expect(["Lua", "Jade"]).toContain(goblinOf("vazio", "").name);
  });
});

describe("GoblinIllustration", () => {
  it.each<[GoblinState, RegExp]>([
    ["vazio", /^(Lua|Jade), /],
    ["carregando", /^(Ada|Fagulha), /],
    ["erro", /^Nyx, /],
    ["sucesso", /^Ferrugem, /],
  ])("no estado %s mostra uma goblin do estado com texto alternativo", (state, alt) => {
    render(<GoblinIllustration state={state} rotationKey="x" />);

    expect(screen.getByRole("img")).toHaveAccessibleName(alt);
  });

  it("reserva o tamanho, carrega tarde e oferece AVIF antes do WebP em 1× e 2×", () => {
    const { container } = render(<GoblinIllustration state="erro" rotationKey="x" />);

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
      expect(source.getAttribute("srcset")).toMatch(/nyx-128w\.\w+ 1x, .*nyx-256w\.\w+ 2x$/);
    }
  });
});
