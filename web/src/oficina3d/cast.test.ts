import { describe, expect, it } from "vitest";

import { TEXT } from "../text";
import { CAST, CAST_IDS, castMember } from "./cast";

describe("CAST", () => {
  it("tem as seis goblins publicadas, cada uma uma vez", () => {
    expect([...CAST_IDS].sort()).toEqual(Object.keys(TEXT.art.goblins).sort());
  });

  it("cada goblin tem imagem estática em AVIF e WebP nas três larguras", () => {
    for (const member of CAST) {
      expect(member.avif.split(", ")).toHaveLength(3);
      expect(member.webp.split(", ")).toHaveLength(3);
      expect(member.avif).toMatch(new RegExp(`oficina-3d-${member.id}-640w`));
      expect(member.thumbnail.avif).toMatch(new RegExp(`oficina-3d-${member.id}-192w`));
    }
  });

  it("acha a goblin pelo id", () => {
    expect(castMember("nyx").name).toBe("Nyx");
  });
});
