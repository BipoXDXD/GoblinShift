import {
  useQuery,
  type QueryClient,
  type QueryKey,
  type UseQueryResult,
} from "@tanstack/react-query";

import { useApiClient } from "../api/ApiClientContext";
import type { ApiClient } from "../api/client";
import { ApiError } from "../api/response";
import { getMe, ME_QUERY_KEY, type Me } from "./api";

/** Quanto o GET /me vale no cache: a navegação entre telas não repete o pedido a cada troca. */
const ME_STALE_MS = 30_000;

/**
 * A conta da sessão, compartilhada pelo cabeçalho, pela Oficina, pelo Acesso e pelo aceite:
 * `null` é sem sessão, `undefined` (sem dado) é carregando ou falha.
 */
export function useMe(): UseQueryResult<Me | null> {
  const client = useApiClient();
  return useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: ({ signal }) => readAccount(client, signal),
    staleTime: ME_STALE_MS,
  });
}

/**
 * A conta pelo GET /me, ou `null` sem sessão. Sem o cookie de sessão nem chama o /me, que só daria
 * 401; com o cookie de uma sessão que acabou, o 401 AUTHENTICATION_REQUIRED também é `null`.
 */
export async function readAccount(client: ApiClient, signal?: AbortSignal): Promise<Me | null> {
  if (!client.hasSessionCookie()) {
    return null;
  }
  try {
    return await getMe(client, signal);
  } catch (error) {
    if (error instanceof Error && problemCode(error) === "AUTHENTICATION_REQUIRED") {
      return null;
    }
    throw error;
  }
}

/** Sem sessão (o /me ainda carregando não conta). */
export function isSignedOut(account: Me | null | undefined): boolean {
  return account === null;
}

/**
 * Faz o GET /me ser relido quando outra chamada mostra que a sessão acabou ou que os termos
 * mudaram: o cabeçalho e o aceite dos termos decidem pelo /me, que é a fonte única desse estado.
 * Ligado no QueryCache e no MutationCache do app (App.tsx). A falha do próprio /me não relê o /me,
 * senão o 401 sem sessão viraria um laço de pedidos.
 */
export function refreshAccountOn(
  queryClient: QueryClient,
): (error: Error, queryKey?: QueryKey) => void {
  return (error, queryKey) => {
    const code = problemCode(error);
    const fromMe = queryKey?.[0] === ME_QUERY_KEY[0];
    if (!fromMe && (code === "AUTHENTICATION_REQUIRED" || code === "TERMS_NOT_ACCEPTED")) {
      void queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY, refetchType: "active" });
    }
  };
}

function problemCode(error: Error | null): string | null {
  return error instanceof ApiError && error.failure.kind === "problem"
    ? error.failure.problem.code
    : null;
}
