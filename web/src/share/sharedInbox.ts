import { peekSharedNames, takeShared } from "./inbox";

/**
 * A Cache API do navegador, ou `null` onde ela não existe (contexto inseguro, jsdom). Sem ela não
 * houve compartilhamento guardado, e a página trata como vencido.
 */
function browserCaches(): CacheStorage | null {
  return "caches" in globalThis && typeof globalThis.caches === "object" ? globalThis.caches : null;
}

/**
 * Os nomes do compartilhamento, pelo relógio do aparelho: o service worker gravou a hora com o
 * mesmo relógio, não com o do servidor.
 */
export function peekShared(id: string): Promise<readonly string[] | "expired"> {
  const caches = browserCaches();
  return caches === null ? Promise.resolve("expired") : peekSharedNames(caches, id, Date.now());
}

/** Os arquivos do compartilhamento, que saem da caixa de entrada. */
export function takeSharedFiles(id: string): Promise<readonly File[] | "expired"> {
  const caches = browserCaches();
  return caches === null ? Promise.resolve("expired") : takeShared(caches, id, Date.now());
}
