import type { Query, QueryClient } from "@tanstack/react-query";

import { ME_QUERY_KEY, type Me } from "./api";

/** Toda query que não é o próprio /me: as telas com dados da conta (execuções, chaves, presets). */
function isAccountData(query: Query): boolean {
  return query.queryKey[0] !== ME_QUERY_KEY[0];
}

/**
 * Tira do cache (e da tela) os dados da conta anterior, sem pedir nada de novo: as queries ativas
 * voltam ao estado inicial e as inativas saem do cache. A query que falhou (o 401 que trouxe até
 * aqui) perde os dados e guarda o erro, para a tela mostrar o convite para entrar em vez de ficar
 * carregando. Não refazer o pedido evita o laço do 401 que limpa e volta.
 */
export function forgetAccountData(queryClient: QueryClient): void {
  for (const query of queryClient.getQueryCache().findAll({ predicate: isAccountData })) {
    const failed = query.state.status === "error" ? query.state : null;
    query.reset();
    if (failed !== null) {
      query.setState({
        ...query.state,
        status: "error",
        error: failed.error,
        errorUpdateCount: failed.errorUpdateCount,
        errorUpdatedAt: failed.errorUpdatedAt,
      });
    }
  }
  queryClient.removeQueries({ predicate: isAccountData, type: "inactive" });
}

/** Tela aberta que o `forgetAccountData` deixou no início, sem dado e sem pedido em voo. */
function isForgottenOnScreen(query: Query): boolean {
  return (
    isAccountData(query) && query.state.status === "pending" && query.state.fetchStatus === "idle"
  );
}

/**
 * Observa o /me. Quando a conta muda (login de outra conta noutra aba, ou a sessão que acabou e
 * deixou o /me sem conta), os dados da anterior saem do cache. A cada /me lido, as telas abertas
 * que ficaram no início sem pedido (pelo esquecimento, aqui ou no 401 do `refreshAccountOn`) são
 * buscadas de novo: nenhuma tela fica carregando sem pedido em voo. A query que falhou guarda o
 * erro e não entra nessa busca, o que corta o laço do 401 (o /me relido não a pede de novo). O
 * primeiro /me só é anotado, e o `clear()` do logout (que tira o /me do cache) zera o anotado.
 * Devolve o `unsubscribe`.
 */
export function watchAccountChange(queryClient: QueryClient): () => void {
  let seen: { readonly id: string | null } | null = null;
  const cache = queryClient.getQueryCache();
  return cache.subscribe((event) => {
    const me = cache.find<Me | null>({ queryKey: ME_QUERY_KEY, exact: true });
    if (event.type === "removed" && me === undefined) {
      seen = null;
      return;
    }
    if (event.type !== "updated" || event.action.type !== "success") {
      return;
    }
    // type-coverage:ignore-next-line -- o evento do TanStack declara `query: Query<any, …>`
    if (me === undefined || event.query !== me) {
      return;
    }
    const id = me.state.data?.id ?? null;
    const previous = seen;
    seen = { id };
    if (previous === null) {
      return;
    }
    if (previous.id !== id) {
      forgetAccountData(queryClient);
    }
    void queryClient.refetchQueries({ predicate: isForgottenOnScreen, type: "active" });
  });
}
