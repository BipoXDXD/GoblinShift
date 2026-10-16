import { describe, expect, it } from "vitest";

import { isPublicArt, provenanceViolations, type ArtFile } from "./check-provenance";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);

function doc(overrides: Record<string, unknown> = {}): unknown {
  return {
    schema: 1,
    rights: [{ id: "fonte externa", vendor: "source-c", basis: [], status: "termos lidos" }],
    generations: {
      "nyx-2d": { tool: "fonte externa", inputs: [] },
      "nyx-3d": { tool: "fonte externa", generation_id: "81ae6621", inputs: ["nyx-2d"], rights: "fonte externa" },
    },
    excluded: [{ name: "Jade", source_id: "3c7aa397-f83d", reason: "terceiros" }],
    assets: [
      { file: "goblins/nyx-128w.avif", sha256: SHA_A, derived_from: "nyx-3d" },
      { file: "goblins/nyx-256w.avif", sha256: SHA_B, derived_from: "nyx-3d" },
    ],
    ...overrides,
  };
}

const FILES: readonly ArtFile[] = [
  { path: "goblins/nyx-128w.avif", sha256: SHA_A },
  { path: "goblins/nyx-256w.avif", sha256: SHA_B },
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
      "goblins/nyx-256w.avif: está no PROVENANCE.json, mas o arquivo não existe",
    ]);
  });

  it("hash diferente do arquivo", () => {
    const files = [FILES[0], { path: "goblins/nyx-256w.avif", sha256: SHA_A }].filter(
      (file) => file !== undefined,
    );

    expect(provenanceViolations(files, doc())).toEqual([
      "goblins/nyx-256w.avif: sha256 diferente do registrado",
    ]);
  });

  it("geração que não existe na cadeia", () => {
    const broken = doc({
      generations: {
        "nyx-3d": { tool: "fonte externa", generation_id: "81ae6621", inputs: ["sumiu"], rights: "fonte externa" },
      },
    });

    expect(provenanceViolations(FILES, broken)).toEqual([
      "goblins/nyx-128w.avif: geração sumiu não registrada",
      "goblins/nyx-256w.avif: geração sumiu não registrada",
    ]);
  });

  it("direito que não existe", () => {
    const broken = doc({
      generations: {
        "nyx-2d": { tool: "fonte externa", inputs: [] },
        "nyx-3d": { tool: "fonte externa", generation_id: "81ae", inputs: ["nyx-2d"], rights: "nenhum" },
      },
    });

    expect(provenanceViolations(FILES, broken)).toContain(
      "goblins/nyx-128w.avif: nyx-3d cita o direito nenhum, que não está em rights",
    );
  });

  describe("direito e upstream da geração", () => {
    const rights = [
      { id: "fonte externa", vendor: "source-c", basis: [], status: "termos lidos" },
      { id: "rights-a", vendor: "fonte externa", upstream: "source-a", basis: [], status: "x" },
      { id: "rights-b", vendor: "fonte externa", upstream: "source-b", basis: [], status: "x" },
    ];

    function withTwoD(generation: Record<string, unknown>): unknown {
      return doc({
        rights,
        generations: {
          "nyx-2d": { tool: "fonte externa", inputs: [], ...generation },
          "nyx-3d": { tool: "fonte externa", generation_id: "81ae", inputs: ["nyx-2d"], rights: "fonte externa" },
        },
      });
    }

    it("upstream que contém o declarado no direito: aceita", () => {
      const ok = withTwoD({ upstream: "source-a", rights: "rights-a" });

      expect(provenanceViolations(FILES, ok)).toEqual([]);
    });

    it("direito de outro upstream: recusa", () => {
      const wrong = withTwoD({ upstream: "source-a", rights: "rights-b" });

      expect(provenanceViolations(FILES, wrong)).toContain(
        'goblins/nyx-128w.avif: nyx-2d tem upstream "source-a", mas cita o direito rights-b, que vale para "source-b"',
      );
    });

    it("upstream declarado com direito que não distingue upstream: recusa", () => {
      const generic = withTwoD({ upstream: "source-a", rights: "fonte externa" });

      expect(provenanceViolations(FILES, generic)).toContain(
        'goblins/nyx-128w.avif: nyx-2d tem upstream "source-a", mas cita o direito fonte externa, que não declara upstream',
      );
    });

    it("upstream declarado sem direito: recusa", () => {
      const bare = withTwoD({ upstream: "source-a" });

      expect(provenanceViolations(FILES, bare)).toContain(
        'goblins/nyx-128w.avif: nyx-2d tem upstream "source-a", mas não cita direito',
      );
    });

    it("direito por upstream em geração sem upstream declarado: recusa", () => {
      const silent = withTwoD({ rights: "rights-a" });

      expect(provenanceViolations(FILES, silent)).toContain(
        'goblins/nyx-128w.avif: nyx-2d cita o direito rights-a, que vale para "source-a", mas não declara upstream',
      );
    });
  });

  it("goblin excluída por proveniência não entra no site", () => {
    const tainted = doc({
      generations: {
        "nyx-2d": { tool: "fonte externa", inputs: [] },
        "nyx-3d": {
          tool: "fonte externa",
          generation_id: "3c7aa397-f83d",
          inputs: ["nyx-2d"],
          rights: "fonte externa",
        },
      },
    });

    expect(provenanceViolations(FILES, tainted)).toContain(
      "goblins/nyx-128w.avif: deriva de 3c7aa397-f83d (Jade), excluída: terceiros",
    );
  });

  describe("origem de terceiros ou não verificável", () => {
    const thirdParty = {
      "nyx-ref": { tool: "fonte externa (site)", inputs: [], third_party: "imagem de terceiros" },
      "nyx-2d": { tool: "fonte externa", inputs: ["nyx-ref"] },
      "nyx-3d": { tool: "fonte externa", generation_id: "81ae6621", inputs: ["nyx-2d"], rights: "fonte externa" },
    };
    const NO_DECISION =
      "goblins/nyx-128w.avif: deriva de nyx-ref (imagem de terceiros) sem decisão registrada em exceptions";

    it("sem decisão registrada, recusa", () => {
      expect(provenanceViolations(FILES, doc({ generations: thirdParty }))).toContain(NO_DECISION);
    });

    it("com decisão registrada (quem, quando, por quê), aceita", () => {
      const accepted = doc({
        generations: thirdParty,
        exceptions: [
          {
            generation: "nyx-ref",
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
        exceptions: [{ generation: "nyx-ref", decided_by: "usuário", decided_at: "", reason: "" }],
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
