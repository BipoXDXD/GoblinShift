// Ícones e imagem de compartilhamento ligados no index.html e no manifest: cada caminho aponta para
// um arquivo de public/ com as dimensões declaradas, e a og:image é absoluta (os robôs de
// compartilhamento não resolvem caminho relativo).
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { z } from "zod";

const web = (path: string): string => join(import.meta.dirname, "..", path);
const INDEX_HTML = readFileSync(web("index.html"), "utf8");
const ORIGIN = "https://goblinshift.dev";

/** Largura e altura de um PNG, lidas do cabeçalho IHDR. */
function pngSize(path: string): string {
  const bytes = readFileSync(path);
  return `${String(bytes.readUInt32BE(16))}x${String(bytes.readUInt32BE(20))}`;
}

function linkHref(rel: string, extra = ""): string | undefined {
  const tag = [...INDEX_HTML.matchAll(/<link\b[^>]*>/g)]
    .map(([match]) => match)
    .find((match) => match.includes(`rel="${rel}"`) && match.includes(extra));
  return tag === undefined ? undefined : /href="([^"]+)"/.exec(tag)?.[1];
}

function metaContent(property: string): string | undefined {
  const tag = [...INDEX_HTML.matchAll(/<meta\b[^>]*>/g)]
    .map(([match]) => match)
    .find((match) => match.includes(`property="${property}"`));
  return tag === undefined ? undefined : /content="([^"]+)"/.exec(tag)?.[1];
}

const manifestSchema = z.object({
  name: z.string(),
  short_name: z.string(),
  icons: z.array(z.object({ src: z.string(), sizes: z.string(), type: z.string() })),
});

describe("index.html: ícones", () => {
  it.each([
    ["icon", 'type="image/svg+xml"', "/favicon.svg"],
    ["icon", 'sizes="32x32"', "/favicon.ico"],
    ["apple-touch-icon", "", "/apple-touch-icon.png"],
    ["manifest", "", "/manifest.webmanifest"],
  ])("rel=%s %s aponta para %s, que existe em public/", (rel, extra, href) => {
    expect(linkHref(rel, extra)).toBe(href);
    expect(existsSync(web(`public${href}`))).toBe(true);
  });

  it("o apple-touch-icon tem 180×180", () => {
    expect(pngSize(web("public/apple-touch-icon.png"))).toBe("180x180");
  });
});

describe("manifest.webmanifest", () => {
  const manifest = manifestSchema.parse(
    JSON.parse(readFileSync(web("public/manifest.webmanifest"), "utf8")),
  );

  it("traz o nome do site", () => {
    expect(manifest.name).toBe("GoblinShift");
    expect(manifest.short_name).toBe("GoblinShift");
  });

  it("declara os PNGs de 192 e 512 com o tamanho real de cada arquivo", () => {
    const pngs = manifest.icons.filter((icon) => icon.type === "image/png");

    expect(pngs.map((icon) => icon.sizes)).toEqual(["192x192", "512x512"]);
    for (const icon of pngs) {
      expect(pngSize(web(`public${icon.src}`))).toBe(icon.sizes);
    }
  });
});

describe("index.html: compartilhamento (Open Graph)", () => {
  it("og:image é absoluta, no domínio do site, e o arquivo existe", () => {
    expect(metaContent("og:image")).toBe(`${ORIGIN}/og-image.jpg`);
    expect(existsSync(web("public/og-image.jpg"))).toBe(true);
  });

  it("declara as dimensões e o texto alternativo da imagem", () => {
    expect(metaContent("og:image:width")).toBe("1200");
    expect(metaContent("og:image:height")).toBe("630");
    expect(metaContent("og:image:alt")).toMatch(/GoblinShift/);
  });

  it.each(["og:title", "og:description", "og:type", "og:url", "og:site_name"])(
    "tem %s",
    (property) => {
      expect(metaContent(property)).toBeTruthy();
    },
  );
});
