import { describe, expect, it } from "vitest";

import { provenanceViolations, type ArtFile } from "./check-provenance";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);

function doc(overrides: Record<string, unknown> = {}): unknown {
  return {
    schema: 1,
    rights: [{ id: "rodin", vendor: "hyper3d", basis: [], status: "termos lidos" }],
    generations: {
      "lua-2d": { tool: "Tripo", inputs: [] },
      "lua-3d": { tool: "Rodin", generation_id: "81ae6621", inputs: ["lua-2d"], rights: "rodin" },
    },
    excluded: [{ name: "Jade", rodin: "3c7aa397-f83d", reason: "terceiros" }],
    assets: [
      { file: "goblins/lua-128w.avif", sha256: SHA_A, derived_from: "lua-3d" },
      { file: "goblins/lua-256w.avif", sha256: SHA_B, derived_from: "lua-3d" },
    ],
    ...overrides,
  };
}

const FILES: readonly ArtFile[] = [
  { path: "goblins/lua-128w.avif", sha256: SHA_A },
  { path: "goblins/lua-256w.avif", sha256: SHA_B },
];

describe("provenanceViolations", () => {
  it("cada arquivo com entrada, hash certo e cadeia resolvida: nada a dizer", () => {
    expect(provenanceViolations(FILES, doc())).toEqual([]);
  });

  it("arquivo sem entrada no PROVENANCE.json", () => {
    const files = [...FILES, { path: "goblins/jade-128w.avif", sha256: SHA_A }];

    expect(provenanceViolations(files, doc())).toEqual([
      "goblins/jade-128w.avif: sem entrada no PROVENANCE.json",
    ]);
  });

  it("entrada sem arquivo", () => {
    expect(provenanceViolations(FILES.slice(0, 1), doc())).toEqual([
      "goblins/lua-256w.avif: está no PROVENANCE.json, mas o arquivo não existe",
    ]);
  });

  it("hash diferente do arquivo", () => {
    const files = [FILES[0], { path: "goblins/lua-256w.avif", sha256: SHA_A }].filter(
      (file) => file !== undefined,
    );

    expect(provenanceViolations(files, doc())).toEqual([
      "goblins/lua-256w.avif: sha256 diferente do registrado",
    ]);
  });

  it("geração que não existe na cadeia", () => {
    const broken = doc({
      generations: {
        "lua-3d": { tool: "Rodin", generation_id: "81ae6621", inputs: ["sumiu"], rights: "rodin" },
      },
    });

    expect(provenanceViolations(FILES, broken)).toEqual([
      "goblins/lua-128w.avif: geração sumiu não registrada",
      "goblins/lua-256w.avif: geração sumiu não registrada",
    ]);
  });

  it("direito que não existe", () => {
    const broken = doc({
      generations: {
        "lua-2d": { tool: "Tripo", inputs: [] },
        "lua-3d": { tool: "Rodin", generation_id: "81ae", inputs: ["lua-2d"], rights: "nenhum" },
      },
    });

    expect(provenanceViolations(FILES, broken)).toContain(
      "goblins/lua-128w.avif: lua-3d cita o direito nenhum, que não está em rights",
    );
  });

  it("goblin excluída por proveniência não entra no site", () => {
    const tainted = doc({
      generations: {
        "lua-2d": { tool: "Tripo", inputs: [] },
        "lua-3d": {
          tool: "Rodin",
          generation_id: "3c7aa397-f83d",
          inputs: ["lua-2d"],
          rights: "rodin",
        },
      },
    });

    expect(provenanceViolations(FILES, tainted)).toContain(
      "goblins/lua-128w.avif: deriva de 3c7aa397-f83d (Jade), excluída: terceiros",
    );
  });

  it("documento fora do formato é um erro só, sem seguir adiante", () => {
    expect(provenanceViolations(FILES, { schema: 2 })).toEqual([
      "PROVENANCE.json fora do formato esperado",
    ]);
  });
});
