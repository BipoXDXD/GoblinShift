import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { watchAccountChange } from "../account/accountCache";
import { refreshAccountOn } from "../account/useMe";
import { queryRetryDelay, shouldRetryQuery } from "../api/queryRetry";

/**
 * O QueryClient do app: leituras com o retry só de falha transitória, o /me relido quando qualquer
 * chamada mostra que a sessão acabou ou que os termos mudaram (useMe.ts), e o cache das telas
 * esquecido quando a conta muda ou a sessão acaba (accountCache.ts). O observador vive o tempo do
 * cliente, que é o do app.
 */
export function createAppQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        // Roda depois do dispatch do erro, mas antes do render: a limpeza do cache chega à tela
        // junto com o erro só porque o scheduler do notifyManager entrega as notificações depois
        // (setTimeout(0)). Com um scheduler síncrono, a tela pintaria o erro com os dados antigos.
        refreshAccountOn(client)(error, query.queryKey);
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        refreshAccountOn(client)(error);
      },
    }),
    defaultOptions: { queries: { retry: shouldRetryQuery, retryDelay: queryRetryDelay } },
  });
  watchAccountChange(client);
  return client;
}
