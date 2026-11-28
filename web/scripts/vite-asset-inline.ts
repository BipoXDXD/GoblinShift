// Arte (src/assets/art/) nunca vira data: URL no bundle, por menor que seja. A vitrine de portfólio
// acha as goblins de cada captura pelo nome do arquivo servido (e2e/vitrine/vitrine.ts, D3 a): um
// emblema de 2 KB embutido passava sem nome e escapava da conferência.
const ART_DIR = /[\\/]src[\\/]assets[\\/]art[\\/]/;

/**
 * `build.assetsInlineLimit` do Vite (que também passa o conteúdo, sem uso aqui): `false` não
 * embute; `undefined` deixa o limite padrão.
 */
export function assetsInlineLimit(filePath: string): boolean | undefined {
  return ART_DIR.test(filePath) ? false : undefined;
}
