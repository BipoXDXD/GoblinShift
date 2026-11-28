import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";

import { SessionChannelProvider } from "../account/SessionChannelContext";
import { ApiClientProvider } from "../api/ApiClientContext";
import type { ApiClient } from "../api/client";

interface RenderRoutesOptions {
  readonly routes: RouteObject[];
  readonly path: string;
  readonly client: ApiClient;
  /** Entradas antes de `path` no histórico, para testar o Voltar. */
  readonly visited?: readonly string[];
  /** Outro QueryClient, como o do app (`createAppQueryClient`); por padrão, um sem retry. */
  readonly queryClient?: QueryClient;
  /** O canal da sessão entre abas, como o do app; por padrão, nenhum (navegador sem o canal). */
  readonly sessionChannel?: BroadcastChannel | null;
}

/** Renderiza as rotas num router em memória, com o cliente da API e um QueryClient novo. */
export function renderRoutes({
  routes,
  path,
  client,
  visited = [],
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  sessionChannel = null,
}: RenderRoutesOptions): RenderResult & {
  readonly router: ReturnType<typeof createMemoryRouter>;
  readonly queryClient: QueryClient;
} {
  const router = createMemoryRouter(routes, {
    initialEntries: [...visited, path],
    initialIndex: visited.length,
  });
  const view = render(
    <ApiClientProvider client={client}>
      <QueryClientProvider client={queryClient}>
        <SessionChannelProvider channel={sessionChannel}>
          <RouterProvider router={router} />
        </SessionChannelProvider>
      </QueryClientProvider>
    </ApiClientProvider>,
  );
  return { ...view, router, queryClient };
}

/** Caminho, query e fragmento atuais do router de memória, como a barra de endereço mostraria. */
export function currentUrl(router: ReturnType<typeof createMemoryRouter>): string {
  // type-coverage:ignore-next-line -- o Location do React Router declara `state: any`
  const { pathname, search, hash } = router.state.location;
  return `${pathname}${search}${hash}`;
}
