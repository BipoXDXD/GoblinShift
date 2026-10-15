import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";

import { refreshAccountOn } from "../account/useMe";
import { ApiClientProvider } from "../api/ApiClientContext";
import { createApiClient } from "../api/client";
import { readCsrfCookie } from "../api/csrf";
import { shouldRetryQuery } from "../api/queryRetry";
import { createContentUploader } from "../upload/xhr";
import { createRoutes } from "./routes";

/** Tentativas de um envio que recebe 429 RATE_LIMITED, contando a primeira. */
const UPLOAD_RATE_LIMIT_ATTEMPTS = 5;
/** Espera quando o 429 vem sem Retry-After; o mesmo intervalo do polling (2.2). */
const UPLOAD_RATE_LIMIT_FALLBACK_MS = 2_000;

export function App(): ReactElement {
  // Um cliente e um router por montagem: testes e Strict Mode não compartilham cache nem histórico.
  const [queryClient] = useState(createQueryClient);
  const [apiClient] = useState(() =>
    createApiClient({
      fetch: (input, init) => window.fetch(input, init),
      csrfToken: readCsrfCookie,
      now: Date.now,
    }),
  );
  const [router] = useState(() =>
    createBrowserRouter(
      createRoutes({
        uploadServices: {
          client: apiClient,
          uploader: createContentUploader({
            createXhr: () => new XMLHttpRequest(),
            csrfToken: readCsrfCookie,
            now: Date.now,
          }),
          rateLimit: {
            maxAttempts: UPLOAD_RATE_LIMIT_ATTEMPTS,
            fallbackDelayMs: UPLOAD_RATE_LIMIT_FALLBACK_MS,
            random: Math.random,
          },
        },
        now: Date.now,
      }),
    ),
  );
  return (
    <ApiClientProvider client={apiClient}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ApiClientProvider>
  );
}

/**
 * Leituras com o retry só de falha transitória, e o /me relido quando qualquer chamada mostra que a
 * sessão acabou ou que os termos mudaram (useMe.ts).
 */
function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        refreshAccountOn(client)(error, query.queryKey);
      },
    }),
    mutationCache: new MutationCache({
      onError: (error) => {
        refreshAccountOn(client)(error);
      },
    }),
    defaultOptions: { queries: { retry: shouldRetryQuery } },
  });
  return client;
}
