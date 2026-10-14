import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";

import { ApiClientProvider } from "../api/ApiClientContext";
import type { ApiClient } from "../api/client";

interface RenderRoutesOptions {
  readonly routes: RouteObject[];
  readonly path: string;
  readonly client: ApiClient;
}

/** Renderiza as rotas num router em memória, com o cliente da API e um QueryClient novos. */
export function renderRoutes({
  routes,
  path,
  client,
}: RenderRoutesOptions): RenderResult & { readonly router: ReturnType<typeof createMemoryRouter> } {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <ApiClientProvider client={client}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ApiClientProvider>,
  );
  return { ...view, router };
}
