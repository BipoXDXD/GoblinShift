/** O endereço de um pedido de `fetch`, qualquer que seja a forma em que ele chegou. */
export function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

/**
 * `fetchSample` falso: responde sempre com `body` (ou com o status de erro dado) e guarda o
 * endereço de cada pedido em `requested`.
 */
export function fakeSampleFetch(response: { readonly body?: string; readonly status?: number }): {
  readonly fetchSample: typeof fetch;
  readonly requested: readonly string[];
} {
  const requested: string[] = [];
  const fetchSample: typeof fetch = (input) => {
    requested.push(urlOf(input));
    return Promise.resolve(new Response(response.body ?? "", { status: response.status ?? 200 }));
  };
  return { fetchSample, requested };
}
