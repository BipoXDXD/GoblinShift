import { describe, expect, it } from "vitest";

import { returnPathFrom } from "./returnParam";

describe("returnPathFrom", () => {
  it.each([
    ["", "/"],
    ["?volta=%2Facesso", "/acesso"],
    ["?volta=%2Fhistorico%3Festado%3Dfailed", "/historico?estado=failed"],
    ["?volta=%2F%2Fevil.example", "/"],
    ["?volta=https%3A%2F%2Fevil.example", "/"],
    ["?volta=javascript%3Aalert(1)", "/"],
    ["?volta=", "/"],
  ])("%j volta para %s", (search, expected) => {
    expect(returnPathFrom(search)).toBe(expected);
  });
});
