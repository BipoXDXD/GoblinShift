import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CAST,
  GOBLIN_STATES,
  goblinOf,
  GoblinIllustration,
  goblinsOf,
  type GoblinState,
} from "./GoblinIllustration";

const KEYS = Array.from({ length: 200 }, (_, index) => `chave-${String(index)}`);

describe("goblinsOf", () => {
  it("cada estado tem as suas goblins, e as cinco aparecem uma vez só", () => {
    const names = GOBLIN_STATES.map((state) => goblinsOf(state).map((goblin) => goblin.name));

    expect(names).toEqual([["Jade"], ["Rúbia", "Fagulha"], ["Nyx"], ["Ferrugem"]]);
  });
});

describe("CAST", () => {
  it("o elenco tem as cinco goblins publicadas", () => {
    expect(CAST.map((goblin) => goblin.name)).toEqual([
      "Rúbia",
      "Fagulha",
      "Ferrugem",
      "Jade",
      "Nyx",
    ]);
  });
});

describe("goblinOf (rotação estável)", () => {
  it("a mesma chave dá sempre a mesma goblin", () => {
    for (const key of KEYS) {
      expect(goblinOf("vazio", key).name).toBe(goblinOf("vazio", key).name);
    }
  });

  it.each<[GoblinState, string[]]>([["carregando", ["Fagulha", "Rúbia"]]])(
    "no estado %s, chaves diferentes alternam entre %j",
    (state, expected) => {
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
    },
  );

  it("estado com uma goblin só ignora a chave", () => {
    expect(new Set(KEYS.map((key) => goblinOf("erro", key).name))).toEqual(new Set(["Nyx"]));
  });

  it("chave vazia ainda escolhe uma goblin do estado", () => {
    expect(goblinOf("vazio", "").name).toBe("Jade");
  });
});

describe("GoblinIllustration", () => {
  it.each<[GoblinState, RegExp]>([
    ["vazio", /^Jade, /],
    ["carregando", /^(Rúbia|Fagulha), /],
    ["erro", /^Nyx, /],
    ["sucesso", /^Ferrugem, /],
  ])("no estado %s mostra uma goblin do estado com texto alternativo", (state, alt) => {
    render(<GoblinIllustration state={state} rotationKey="x" />);

    expect(screen.getByRole("img")).toHaveAccessibleName(alt);
  });

  it("reserva a caixa de destaque e carrega tarde", () => {
    render(<GoblinIllustration state="erro" rotationKey="x" />);

    const img = screen.getByRole("img");
    // 4:5, como os arquivos: com width e height o navegador reserva a caixa antes de baixar (CLS).
    expect(img).toHaveAttribute("width", "256");
    expect(img).toHaveAttribute("height", "320");
    expect(img).toHaveAttribute("loading", "lazy");
    expect(img).toHaveAttribute("decoding", "async");
  });

  it("oferece AVIF antes do WebP, de 256 a 768 px, com sizes da caixa", () => {
    const { container } = render(<GoblinIllustration state="erro" rotationKey="x" />);

    const sources = [...container.querySelectorAll("source")];
    expect(sources.map((source) => source.getAttribute("type"))).toEqual([
      "image/avif",
      "image/webp",
    ]);
    for (const source of sources) {
      expect(source.getAttribute("srcset")).toMatch(
        /nyx-256w\.\w+ 256w, .*nyx-384w\.\w+ 384w, .*nyx-512w\.\w+ 512w, .*nyx-768w\.\w+ 768w$/,
      );
      expect(source.getAttribute("sizes")).toBe("(min-width: 768px) 256px, 192px");
    }
  });
});
