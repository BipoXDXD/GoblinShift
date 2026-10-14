import monoLicense from "@fontsource-variable/atkinson-hyperlegible-mono/LICENSE?raw";
import monoPackage from "@fontsource-variable/atkinson-hyperlegible-mono/package.json";
import nextLicense from "@fontsource-variable/atkinson-hyperlegible-next/LICENSE?raw";
import nextPackage from "@fontsource-variable/atkinson-hyperlegible-next/package.json";
import bricolageLicense from "@fontsource-variable/bricolage-grotesque/LICENSE?raw";
import bricolagePackage from "@fontsource-variable/bricolage-grotesque/package.json";
import { describe, expect, it } from "vitest";

import licensesDoc from "../../LICENCAS-FONTES.md?raw";
import fontsCss from "./fonts.css?raw";

const FONTS = [
  { pkg: bricolagePackage, license: bricolageLicense },
  { pkg: nextPackage, license: nextLicense },
  { pkg: monoPackage, license: monoLicense },
];

describe.each(FONTS)("$pkg.name", ({ pkg, license }) => {
  it("é OFL-1.1, sem nome reservado", () => {
    expect(pkg.license).toBe("OFL-1.1");
    expect(license).toContain("SIL OPEN FONT LICENSE Version 1.1");
    // O cabeçalho, antes do texto da OFL, é onde o autor declararia um nome reservado.
    const header = license.split("SIL OPEN FONT LICENSE")[0] ?? "";
    expect(header).not.toMatch(/Reserved Font Name/i);
  });

  it("tem o aviso de copyright e a versão registrados em LICENCAS-FONTES.md", () => {
    const copyright = license.split("\n")[0] ?? "";
    expect(licensesDoc).toContain(copyright);
    const versionRow = new RegExp(
      `\`${pkg.name}\`\\s*\\|\\s*${pkg.version.replaceAll(".", "\\.")}\\s*\\|`,
    );
    expect(licensesDoc).toMatch(versionRow);
  });

  it("é a origem dos arquivos declarados no fonts.css", () => {
    expect(fontsCss).toContain(`url("${pkg.name}/files/`);
  });
});

it("o fonts.css não declara fonte de fora dos pacotes registrados", () => {
  const sources = [...fontsCss.matchAll(/url\("([^"]+)"\)/g)].map(([, url]) => url ?? "");
  const registered = FONTS.map(({ pkg }) => `${pkg.name}/files/`);
  expect(sources.length).toBeGreaterThan(0);
  expect(sources.filter((url) => !registered.some((prefix) => url.startsWith(prefix)))).toEqual([]);
});
