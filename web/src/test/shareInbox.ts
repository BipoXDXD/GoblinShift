import { vi } from "vitest";

import { SHARE_FIELD, storeShared } from "../share/inbox";

/** Origem das chaves relativas, como a do service worker no navegador. */
const ORIGIN = "https://goblinshift.test";

/**
 * `CacheStorage` em memória, só com o que a caixa de entrada do compartilhamento usa (`open`,
 * `put`, `match`, `keys` e `delete`); o resto falha, para um uso novo não passar em silêncio. O
 * jsdom não tem a Cache API.
 */
export function fakeCacheStorage(): CacheStorage {
  return new FakeCacheStorage();
}

class FakeCacheStorage implements CacheStorage {
  readonly #stores = new Map<string, FakeCache>();

  open(name: string): Promise<Cache> {
    const cache = this.#stores.get(name) ?? new FakeCache();
    this.#stores.set(name, cache);
    return Promise.resolve(cache);
  }

  delete(): Promise<boolean> {
    return unused("CacheStorage.delete");
  }

  has(): Promise<boolean> {
    return unused("CacheStorage.has");
  }

  keys(): Promise<string[]> {
    return unused("CacheStorage.keys");
  }

  match(): Promise<Response | undefined> {
    return unused("CacheStorage.match");
  }
}

class FakeCache implements Cache {
  readonly #entries = new Map<string, Response>();

  put(request: RequestInfo | URL, response: Response): Promise<void> {
    this.#entries.set(urlOf(request), response);
    return Promise.resolve();
  }

  match(request: RequestInfo | URL): Promise<Response | undefined> {
    return Promise.resolve(this.#entries.get(urlOf(request))?.clone());
  }

  keys(): Promise<readonly Request[]> {
    return Promise.resolve([...this.#entries.keys()].map((url) => new Request(url)));
  }

  delete(request: RequestInfo | URL): Promise<boolean> {
    return Promise.resolve(this.#entries.delete(urlOf(request)));
  }

  add(): Promise<void> {
    return unused("Cache.add");
  }

  addAll(): Promise<void> {
    return unused("Cache.addAll");
  }

  matchAll(): Promise<readonly Response[]> {
    return unused("Cache.matchAll");
  }
}

function unused(method: string): Promise<never> {
  return Promise.reject(new Error(`${method} não faz parte do dublê`));
}

function urlOf(request: RequestInfo | URL): string {
  const raw = request instanceof Request ? request.url : request.toString();
  return new URL(raw, ORIGIN).href;
}

/**
 * Troca a `Response` global por uma que guarda o `Blob` do corpo como veio. A `Response` do Node
 * não reconhece o `File` do jsdom e grava o texto "[object File]" no lugar do conteúdo; a caixa de
 * entrada só usa o corpo como `Blob` e os cabeçalhos, como o service worker faz no navegador.
 */
export function useBlobResponse(): void {
  vi.stubGlobal("Response", BlobResponse);
}

class BlobResponse {
  readonly headers: Headers;
  readonly #body: Blob;

  constructor(body: Blob | null, init?: ResponseInit) {
    this.#body = body ?? new Blob([]);
    this.headers = new Headers(init?.headers);
  }

  blob(): Promise<Blob> {
    return Promise.resolve(this.#body);
  }

  text(): Promise<string> {
    return this.#body.text();
  }

  clone(): BlobResponse {
    return new BlobResponse(this.#body, { headers: this.headers });
  }
}

/**
 * Grava um compartilhamento na caixa de entrada como o service worker faria, com a `Response` que
 * guarda o `Blob` só durante a gravação: a API falsa da tela continua com a `Response` do Node.
 */
export async function seedShared(
  caches: CacheStorage,
  files: readonly File[],
  { id, at = Date.now() }: { readonly id: string; readonly at?: number },
): Promise<void> {
  const original = globalThis.Response;
  vi.stubGlobal("Response", BlobResponse);
  try {
    const form = new FormData();
    for (const file of files) {
      form.append(SHARE_FIELD, file);
    }
    await storeShared(caches, form, { now: at, newId: () => id });
  } finally {
    vi.stubGlobal("Response", original);
  }
}
