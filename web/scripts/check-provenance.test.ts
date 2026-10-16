import { describe, expect, it } from "vitest";

import { isPublicArt, provenanceViolations, type ArtFile } from "./check-provenance";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);

function doc(overrides: Record<string, unknown> = {}): unknown {
  return {
    schema: 1,
    rights: [{ id: "fonte externa", vendor: "source-c", basis: [], status: "termos lidos" }],
    generations: {
      "lua-2d": { tool: "fonte externa", inputs: [] },
      "lua-3d": { tool: "fonte externa", generation_id: "81ae6621", inputs: ["lua-2d"], rights: "fonte externa" },
    },
    excluded: [{ name: "Jade", source_id: "3c7aa397-f83d", reason: "terceiros" }],
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
        "lua-3d": { tool: "fonte externa", generation_id: "81ae6621", inputs: ["sumiu"], rights: "fonte externa" },
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
        "lua-2d": { tool: "fonte externa", inputs: [] },
        "lua-3d": { tool: "fonte externa", generation_id: "81ae", inputs: ["lua-2d"], rights: "nenhum" },
      },
    });

    expect(provenanceViolations(FILES, broken)).toContain(
      "goblins/lua-128w.avif: lua-3d cita o direito nenhum, que não está em rights",
    );
  });

  it("goblin excluída por proveniência não entra no site", () => {
    const tainted = doc({
      generations: {
        "lua-2d": { tool: "fonte externa", inputs: [] },
        "lua-3d": {
          tool: "fonte externa",
          generation_id: "3c7aa397-f83d",
          inputs: ["lua-2d"],
          rights: "fonte externa",
        },
      },
    });

    expect(provenanceViolations(FILES, tainted)).toContain(
      "goblins/lua-128w.avif: deriva de 3c7aa397-f83d (Jade), excluída: terceiros",
    );
  });

  describe("origem de terceiros ou não verificável", () => {
    const thirdParty = {
      "lua-ref": { tool: "fonte externa (site)", inputs: [], third_party: "imagem de terceiros" },
      "lua-2d": { tool: "fonte externa", inputs: ["lua-ref"] },
      "lua-3d": { tool: "fonte externa", generation_id: "81ae6621", inputs: ["lua-2d"], rights: "fonte externa" },
    };
    const NO_DECISION =
      "goblins/lua-128w.avif: deriva de lua-ref (imagem de terceiros) sem decisão registrada em exceptions";

    it("sem decisão registrada, recusa", () => {
      expect(provenanceViolations(FILES, doc({ generations: thirdParty }))).toContain(NO_DECISION);
    });

    it("com decisão registrada (quem, quando, por quê), aceita", () => {
      const accepted = doc({
        generations: thirdParty,
        exceptions: [
          {
            generation: "lua-ref",
            decided_by: "usuário",
            decided_at: "2026-10-15",
            reason: "assume o risco",
          },
        ],
      });

      expect(provenanceViolations(FILES, accepted)).toEqual([]);
    });

    it("decisão sem data ou sem motivo não vale", () => {
      const incomplete = doc({
        generations: thirdParty,
        exceptions: [{ generation: "lua-ref", decided_by: "usuário", decided_at: "", reason: "" }],
      });

      expect(provenanceViolations(FILES, incomplete)).toEqual([
        "PROVENANCE.json fora do formato esperado",
      ]);
    });

    it("decisão sobre outra geração não abre exceção para esta", () => {
      const other = doc({
        generations: thirdParty,
        exceptions: [
          { generation: "outra", decided_by: "usuário", decided_at: "2026-10-15", reason: "x" },
        ],
      });

      expect(provenanceViolations(FILES, other)).toContain(NO_DECISION);
    });
  });

  it("documento fora do formato é um erro só, sem seguir adiante", () => {
    expect(provenanceViolations(FILES, { schema: 2 })).toEqual([
      "PROVENANCE.json fora do formato esperado",
    ]);
  });
});

// Os ícones e a imagem de compartilhamento ficam em public/ (URL fixa, sem hash): também são arte
// e entram no registro, com o prefixo `public/`.
describe("isPublicArt", () => {
  it.each(["favicon.svg", "favicon.ico", "apple-touch-icon.png", "icon-512.png", "og-image.jpg"])(
    "%s é arte",
    (name) => {
      expect(isPublicArt(name)).toBe(true);
    },
  );

  it.each(["theme-init.js", "manifest.webmanifest", ".well-known/security.txt", "robots.txt"])(
    "%s não é arte",
    (name) => {
      expect(isPublicArt(name)).toBe(false);
    },
  );
});
