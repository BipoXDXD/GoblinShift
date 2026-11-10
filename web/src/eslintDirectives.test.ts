import { describe, expect, it } from "vitest";

/*
 * Toda diretiva que desliga uma regra do ESLint diz o porquê depois de ` -- ` (sintaxe do próprio
 * ESLint). O ESLint não tem regra nativa para exigir a descrição e o plugin que a tem
 * (eslint-comments) seria uma dependência a mais só para isso; esta leitura cobre o mesmo.
 */

const DISABLE_DIRECTIVE = /eslint-disable(?:-next-line|-line)?\b[^\n]*/g;
const DESCRIPTION = /\s--\s+\S/;

/** Diretivas `eslint-disable` sem descrição, como aparecem no arquivo. */
function undescribedDirectives(source: string): string[] {
  return [...source.matchAll(DISABLE_DIRECTIVE)]
    .map(([directive]) => directive.trim())
    .filter((directive) => !DESCRIPTION.test(directive));
}

describe("undescribedDirectives", () => {
  it.each([
    "// eslint-disable-next-line @typescript-eslint/unbound-method",
    "// eslint-disable-line no-console",
    "/* eslint-disable eqeqeq */",
    "// eslint-disable-next-line no-console --",
  ])("recusa %s", (source) => {
    expect(undescribedDirectives(source)).toHaveLength(1);
  });

  it.each([
    "// eslint-disable-next-line @typescript-eslint/unbound-method -- chamado com Reflect.apply",
    "/* eslint-disable eqeqeq -- compara com null de propósito */",
    "// eslint-enable eqeqeq",
    "const x = 1;",
  ])("aceita %s", (source) => {
    expect(undescribedDirectives(source)).toEqual([]);
  });
});

const SOURCES = import.meta.glob<string>(
  [
    "./**/*.{ts,tsx}",
    "../e2e/**/*.ts",
    "../scripts/**/*.ts",
    "../*.{ts,js}",
    "!./eslintDirectives.test.ts",
  ],
  { query: "?raw", import: "default", eager: true },
);

describe("diretivas do ESLint no código", () => {
  it("lê o app, o e2e e os scripts", () => {
    const paths = Object.keys(SOURCES);
    expect(paths.some((path) => path.startsWith("../e2e/"))).toBe(true);
    expect(paths.some((path) => path.startsWith("../scripts/"))).toBe(true);
    expect(paths.some((path) => path.startsWith("./"))).toBe(true);
  });

  it.each(Object.entries(SOURCES))("%s descreve cada diretiva", (_path, source) => {
    expect(undescribedDirectives(source)).toEqual([]);
  });
});
