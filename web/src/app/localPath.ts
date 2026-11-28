// Num módulo sem dependências: o service worker (sw.ts) também o usa, e o build dele não deve
// puxar o router.

/**
 * Caminho deste site: uma barra e, depois dela, nem outra barra nem contrabarra. `//evil.com` e
 * `/\evil.com` o navegador lê como outro site.
 */
export const LOCAL_PATH = /^\/(?![/\\])/;
