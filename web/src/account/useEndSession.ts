import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import type { ApiClient } from "../api/client";

/**
 * Ação que encerra a sessão (sair, sair de todos, excluir a conta). Com o 2xx o servidor já
 * revogou e mandou o navegador apagar cookies e armazenamento (Clear-Site-Data); aqui some o cache
 * da conta anterior e a interface vai para `destination`, sem nada da sessão que acabou.
 */
export function useEndSession(
  end: (client: ApiClient) => Promise<void>,
  destination: string,
): UseMutationResult<void, Error, void> {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: () => end(client),
    onSuccess: async () => {
      queryClient.clear();
      await navigate(destination, { replace: true });
    },
  });
}
