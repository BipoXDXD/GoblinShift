/**
 * A URL de uma chamada saiu da origem da interface (ou nem é uma URL): o pedido não é feito, para o
 * CSRF e o cookie de sessão nunca seguirem para outro host.
 */
export class CrossOriginUrlError extends Error {
  readonly url: string;

  constructor(url: string) {
    super(`URL fora da origem da interface: ${url}`);
    this.name = "CrossOriginUrlError";
    this.url = url;
  }
}

/**
 * Resolve `url` contra `origin` e só a devolve se continuar nela.
 *
 * Cobre caminho relativo, URL absoluta e `//host` (relativa ao esquema), que resolveria para
 * outro host.
 */
export function sameOriginUrl(url: string, origin: string): URL {
  let resolved: URL;
  try {
    resolved = new URL(url, origin);
  } catch {
    // URL que nem se resolve não tem origem a conferir: recusa como fora da origem.
    throw new CrossOriginUrlError(url);
  }
  if (resolved.origin !== new URL(origin).origin) {
    throw new CrossOriginUrlError(url);
  }
  return resolved;
}
