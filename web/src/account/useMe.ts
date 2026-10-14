import {
  useQuery,
  type QueryClient,
  type QueryKey,
  type UseQueryResult,
} from "@tanstack/react-query";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { getMe, ME_QUERY_KEY, type Me } from "./api";

/** Quanto o GET /me vale no cache: a navegação entre telas não repete o pedido a cada troca. */
const ME_STALE_MS = 30_000;

/** A conta da sessão (GET /me), compartilhada pelo cabeçalho, pelo Acesso e pelo aceite. */
export function useMe(): UseQueryResult<Me> {
  const client = useApiClient();
  return useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: ({ signal }) => getMe(client, signal),
    staleTime: ME_STALE_MS,
  });
}

/** Sem sessão: o GET /me respondeu 401 AUTHENTICATION_REQUIRED. */
export function isSignedOut(error: Error | null): boolean {
  return problemCode(error) === "AUTHENTICATION_REQUIRED";
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
