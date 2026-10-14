import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";

import { ApiClientProvider } from "../api/ApiClientContext";
import { createApiClient } from "../api/client";
import { readCsrfCookie } from "../api/csrf";
import { HomePage } from "./HomePage";
import { NotFoundPage } from "./NotFoundPage";

// As telas (Bancada, Execução, Histórico, Acesso e Oficina) entram aqui depois das decisões de design.
const routes = [
  { path: "/", element: <HomePage /> },
  { path: "*", element: <NotFoundPage /> },
];

export function App(): ReactElement {
  // Um cliente e um router por montagem: testes e Strict Mode não compartilham cache nem histórico.
  const [queryClient] = useState(() => new QueryClient());
  const [router] = useState(() => createBrowserRouter(routes));
  const [apiClient] = useState(() =>
    createApiClient({
      fetch: (input, init) => window.fetch(input, init),
      csrfToken: readCsrfCookie,
      now: Date.now,
    }),
  );
  return (
    <ApiClientProvider client={apiClient}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ApiClientProvider>
  );
}
