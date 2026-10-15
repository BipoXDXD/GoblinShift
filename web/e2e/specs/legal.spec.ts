// /termos e /privacidade no build servido: só o texto público chega à página e ao bundle. O
// material da revisão (pendências, ids J-n, notas ao revisor, fontes e a convenção das marcações)
// mora em docs/legal/notas-internas.md, que a SPA não importa.
import { expect, test } from "../support/test";

test.use({ auth: "none" });

const PAGE_MARKERS = [
  /Pendências/i,
  /\bJ-\d/,
  /notas internas/i,
  /Fontes internas/i,
  /Nota para a revisão/i,
  /não publicar sem revisão jurídica/i,
  /Esta seção sai do texto final/i,
  /Trechos entre/i,
  /Notas marcadas como/i,
];

// No JS minificado, `J-1` pode ser aritmética: o bundle só é conferido por frases inteiras.
const BUNDLE_MARKERS = [
  "Pendências antes de publicar",
  "A DEFINIR (J-",
  "Nota para a revisão",
  "Fontes internas",
  "Notas internas",
  "não publicar sem revisão jurídica",
  "Esta seção sai do texto final",
];

for (const [path, title] of [
  ["/termos", "Termos de Uso do GoblinShift"],
  ["/privacidade", "Aviso de Privacidade do GoblinShift"],
] as const) {
  test(`${path} mostra o aviso de rascunho e nenhum material interno`, async ({ page }) => {
    const scripts: Promise<string>[] = [];
    page.on("response", (response) => {
      if (response.request().resourceType() === "script") {
        scripts.push(response.text());
      }
    });

    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Rascunho" })).toContainText(
      "ainda não passou por revisão jurídica",
    );

    const shown = await page.getByRole("article").innerText();
    for (const marker of PAGE_MARKERS) {
      expect(shown).not.toMatch(marker);
    }
    expect(shown).toContain("[[A DEFINIR");

    const bundle = (await Promise.all(scripts)).join("\n");
    expect(bundle).toContain("A DEFINIR");
    for (const marker of BUNDLE_MARKERS) {
      expect(bundle).not.toContain(marker);
    }
  });
}
