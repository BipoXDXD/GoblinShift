// Rasteriza no Chromium do Playwright o que o ImageMagick não desenha bem: o favicon.svg em 16,
// 32 e 48 px (para o favicon.ico) e a imagem de compartilhamento de 1200×630 com o selo b1 e o
// título na Bricolage Grotesque do site. Chamado por gerar-marca.sh, de dentro de web/.
//
// Uso (cwd = web/): node ../scripts/arte/render-marca.mjs FAVICON_SVG SELO_PNG SAIDA_DIR
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(join(process.cwd(), "package.json"));
const { chromium } = require("@playwright/test");

const [faviconSvg, sealPng, outDir] = process.argv.slice(2).map((path) => resolve(path));
if (outDir === undefined) {
  console.error("uso: render-marca.mjs FAVICON_SVG SELO_PNG SAIDA_DIR");
  process.exit(2);
}

const FAVICON_SIZES = [16, 32, 48];
const OG = { width: 1200, height: 630 };
const FONT = resolve(
  "node_modules/@fontsource-variable/bricolage-grotesque/files/bricolage-grotesque-latin-wght-normal.woff2",
);
// Paleta Taverna escura (web/src/styles/tokens.css): wood-900, wood-950, copper-400, wood-50 e wood-300.
const WOOD = "#231813";
const WOOD_DEEP = "#150c08";
const COPPER = "#e59356";
const CREAM = "#fcf8f3";
const CREAM_MUTED = "#dbcfc4";

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  const svg = readFileSync(faviconSvg, "utf8");
  for (const size of FAVICON_SIZES) {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(
      `<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
    );
    await page.screenshot({ path: join(outDir, `favicon-${size}.png`), omitBackground: true });
  }

  const html = join(outDir, "og.html");
  writeFileSync(
    html,
    `<!doctype html><meta charset="utf-8"><style>
@font-face{font-family:Bricolage;src:url("${pathToFileURL(FONT).href}") format("woff2");font-weight:200 800}
html,body{margin:0}
body{width:${OG.width}px;height:${OG.height}px;display:flex;align-items:center;gap:64px;padding:0 88px;box-sizing:border-box;
background:radial-gradient(circle at 26% 50%, #372922 0, ${WOOD} 55%, ${WOOD_DEEP} 100%);font-family:Bricolage,sans-serif}
img{width:auto;height:520px;flex:none}
h1{margin:0;color:${CREAM};font-size:96px;font-weight:700;line-height:1;letter-spacing:-0.02em}
p{margin:24px 0 0;color:${CREAM_MUTED};font-size:36px;font-weight:400;line-height:1.25;max-width:14em}
.band{position:absolute;left:0;right:0;top:0;height:12px;background:${COPPER}}
</style><div class="band"></div><img src="${pathToFileURL(sealPng).href}" alt=""><div><h1>GoblinShift</h1><p>Arquivos em lote, limpos pelos goblins da oficina.</p></div>`,
  );
  await page.setViewportSize(OG);
  await page.goto(pathToFileURL(html).href);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: join(outDir, "og-image.png") });
} finally {
  await browser.close();
}
