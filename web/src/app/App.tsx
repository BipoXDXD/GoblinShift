import { QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";

import { SessionChannelProvider, useOpenSessionChannel } from "../account/SessionChannelContext";
import { ApiClientProvider } from "../api/ApiClientContext";
import { createApiClient } from "../api/client";
import { readCsrfCookie } from "../api/csrf";
import { createServerClock } from "../api/serverClock";
import { SITE_NOTICE } from "../notice/siteNotice";
import { createContentUploader } from "../upload/xhr";
import { createAppQueryClient } from "./queryClient";
import { createRoutes } from "./routes";

/** Tentativas de um envio que recebe 429 RATE_LIMITED, contando a primeira. */
const UPLOAD_RATE_LIMIT_ATTEMPTS = 5;
/** Espera quando o 429 vem sem Retry-After; o mesmo intervalo do polling (2.2). */
const UPLOAD_RATE_LIMIT_FALLBACK_MS = 2_000;

export function App(): ReactElement {
  // Um cliente e um router por montagem: testes e Strict Mode não compartilham cache nem histórico.
  const [queryClient] = useState(createAppQueryClient);
  // Sair numa aba avisa as outras, que esquecem a conta (sessionSignal.ts). O canal vive o tempo
  // do app e liga no mesmo QueryClient.
  const sessionChannel = useOpenSessionChannel(queryClient);
  // O "agora" de tudo que compara datas do servidor: o relógio local corrigido pelo `Date` das
  // respostas da API (D9).
  const [clock] = useState(() => createServerClock(Date.now));
  const [apiClient] = useState(() =>
    createApiClient({
      fetch: (input, init) => window.fetch(input, init),
      csrfToken: readCsrfCookie,
      now: clock.now,
      observeServerDate: clock.observe,
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
            now: clock.now,
            observeServerDate: clock.observe,
          }),
          rateLimit: {
            maxAttempts: UPLOAD_RATE_LIMIT_ATTEMPTS,
            fallbackDelayMs: UPLOAD_RATE_LIMIT_FALLBACK_MS,
            random: Math.random,
          },
        },
        now: clock.now,
        notice: SITE_NOTICE,
      }),
    ),
  );
  return (
    <ApiClientProvider client={apiClient}>
      <QueryClientProvider client={queryClient}>
        <SessionChannelProvider channel={sessionChannel}>
          <RouterProvider router={router} />
        </SessionChannelProvider>
      </QueryClientProvider>
    </ApiClientProvider>
  );
}
