import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ApiClientProvider, useApiClient } from "./ApiClientContext";
import { createApiClient } from "./client";

describe("useApiClient", () => {
  it("devolve o cliente do provider", () => {
    const client = createApiClient({ fetch, csrfToken: () => null, now: Date.now });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ApiClientProvider client={client}>{children}</ApiClientProvider>
    );

    const { result } = renderHook(() => useApiClient(), { wrapper });

    expect(result.current).toBe(client);
  });

  it("falha alto sem provider", () => {
    expect(() => renderHook(() => useApiClient())).toThrow(/ApiClientProvider/);
  });
});
