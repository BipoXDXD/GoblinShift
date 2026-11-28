import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it, onTestFinished } from "vitest";

import { meBody } from "../test/account";
import { ME_QUERY_KEY, type Me } from "./api";
import { forgetAccountData, watchAccountChange } from "./accountCache";

const RUNS_KEY = ["runs", "history", { limit: 25 }] as const;
const OTHER_ID = "7a1b2c3d-0000-4000-8000-0000000000bb";

function accountOf(id: string): Me {
  return { ...meBody(), id };
}

/** QueryClient com o observador ligado, uma Run da conta A no cache e o /me de A já visto. */
function watchedClient(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  onTestFinished(watchAccountChange(client));
  client.setQueryData(ME_QUERY_KEY, meBody());
  client.setQueryData(RUNS_KEY, { data: ["run-de-A"] });
  return client;
}

describe("forgetAccountData", () => {
  it("esvazia as queries das telas e guarda o /me", () => {
    const client = new QueryClient();
    client.setQueryData(ME_QUERY_KEY, meBody());
    client.setQueryData(RUNS_KEY, { data: ["run-de-A"] });

    forgetAccountData(client);

    expect(client.getQueryData(RUNS_KEY)).toBeUndefined();
    expect(client.getQueryData(ME_QUERY_KEY)).toEqual(meBody());
  });

  it("a query ativa volta ao início sem refazer o pedido", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let calls = 0;
    const observer = new QueryObserver(client, {
      queryKey: RUNS_KEY,
      queryFn: () => {
        calls += 1;
        return Promise.resolve({ data: ["run-de-A"] });
      },
    });
    onTestFinished(observer.subscribe(() => undefined));
    await expect.poll(() => observer.getCurrentResult().data).toBeDefined();

    forgetAccountData(client);

    expect(observer.getCurrentResult().data).toBeUndefined();
    expect(calls).toBe(1);
  });

  it("a query que falhou perde os dados e guarda o erro", () => {
    const client = new QueryClient();
    client.setQueryData(RUNS_KEY, { data: ["run-de-A"] });
    const query = client.getQueryCache().find({ queryKey: RUNS_KEY });
    const failure = new Error("401");
    query?.setState({ ...query.state, status: "error", error: failure });

    forgetAccountData(client);

    expect(query?.state.data).toBeUndefined();
    expect(query?.state.status).toBe("error");
    expect(query?.state.error).toBe(failure);
  });
});

describe("watchAccountChange", () => {
  it("troca do /me de A para B esvazia as queries de A", () => {
    const client = watchedClient();

    client.setQueryData(ME_QUERY_KEY, accountOf(OTHER_ID));

    expect(client.getQueryData(RUNS_KEY)).toBeUndefined();
  });

  it("o /me que vira sem sessão também esvazia", () => {
    const client = watchedClient();

    client.setQueryData(ME_QUERY_KEY, null);

    expect(client.getQueryData(RUNS_KEY)).toBeUndefined();
  });

  it("o mesmo id no /me não limpa nada", () => {
    const client = watchedClient();

    client.setQueryData(ME_QUERY_KEY, { ...meBody(), display_name: "Ana Maria" });

    expect(client.getQueryData(RUNS_KEY)).toEqual({ data: ["run-de-A"] });
  });

  it("o primeiro /me só é anotado", () => {
    const client = new QueryClient();
    onTestFinished(watchAccountChange(client));
    client.setQueryData(RUNS_KEY, { data: ["run"] });

    client.setQueryData(ME_QUERY_KEY, meBody());

    expect(client.getQueryData(RUNS_KEY)).toEqual({ data: ["run"] });
  });

  it("depois do unsubscribe não limpa mais", () => {
    const client = new QueryClient();
    const stop = watchAccountChange(client);
    client.setQueryData(ME_QUERY_KEY, meBody());
    client.setQueryData(RUNS_KEY, { data: ["run-de-A"] });
    stop();

    client.setQueryData(ME_QUERY_KEY, accountOf(OTHER_ID));

    expect(client.getQueryData(RUNS_KEY)).toEqual({ data: ["run-de-A"] });
  });

  it("depois da troca, a tela aberta pede os dados da conta nova", async () => {
    const client = watchedClient();
    let owner = "A";
    const observer = new QueryObserver(client, {
      queryKey: RUNS_KEY,
      queryFn: () => Promise.resolve({ data: [`run-de-${owner}`] }),
      staleTime: Infinity,
    });
    onTestFinished(observer.subscribe(() => undefined));

    owner = "B";
    client.setQueryData(ME_QUERY_KEY, accountOf(OTHER_ID));

    await expect.poll(() => observer.getCurrentResult().data).toEqual({ data: ["run-de-B"] });
  });
});
