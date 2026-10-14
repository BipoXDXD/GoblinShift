import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";

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
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
