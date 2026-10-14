import { createContext, useContext, type ReactElement, type ReactNode } from "react";

import type { ApiClient } from "./client";

const ApiClientContext = createContext<ApiClient | null>(null);

interface ApiClientProviderProps {
  readonly client: ApiClient;
  readonly children: ReactNode;
}

export function ApiClientProvider({ client, children }: ApiClientProviderProps): ReactElement {
  return <ApiClientContext value={client}>{children}</ApiClientContext>;
}

export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext);
  if (client === null) {
    throw new Error("useApiClient precisa de um ApiClientProvider acima na árvore");
  }
  return client;
}
