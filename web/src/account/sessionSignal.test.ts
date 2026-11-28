import { QueryClient } from "@tanstack/react-query";
import { waitFor } from "@testing-library/react";
import { describe, expect, it, onTestFinished, vi } from "vitest";

import { meBody } from "../test/account";
import { fakeChannelNetwork, receivedBy } from "../test/fakeBroadcastChannel";
import { ME_QUERY_KEY } from "./api";
import {
  announceSignedOut,
  openSessionChannel,
  SESSION_CHANNEL,
  watchSignedOutElsewhere,
} from "./sessionSignal";

/** Duas abas da mesma origem: `a` e `b` no canal da sessão, e o QueryClient da aba `b`. */
function twoTabs() {
  const create = fakeChannelNetwork();
  const a = openSessionChannel(create);
  const b = openSessionChannel(create);
  const client = new QueryClient();
  client.setQueryData(ME_QUERY_KEY, meBody());
  return { a, b, client };
}

/** Espera as entregas assíncronas do canal (microtarefas) antes de conferir que nada mudou. */
async function deliveries(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("sessionSignal", () => {
  it("sem BroadcastChannel não abre canal", () => {
    expect(
      openSessionChannel(() => {
        throw new Error("sem BroadcastChannel");
      }),
    ).toBeNull();
    expect(() => {
      announceSignedOut(null);
    }).not.toThrow();
  });

  it("navegador sem BroadcastChannel global: o padrão devolve null", () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    onTestFinished(() => {
      vi.unstubAllGlobals();
    });

    expect(openSessionChannel()).toBeNull();
  });

  it("abre o canal com o nome da sessão", () => {
    const create = fakeChannelNetwork();

    expect(openSessionChannel(create)?.name).toBe(SESSION_CHANNEL);
    expect(SESSION_CHANNEL).toBe("goblinshift-sessao");
  });

  it("avisa com uma mensagem signed-out", async () => {
    const { a, b } = twoTabs();
    if (a === null || b === null) {
      throw new Error("canal não abriu");
    }
    const messages = receivedBy(b);

    announceSignedOut(a);

    await waitFor(() => {
      expect(messages).toEqual([{ type: "signed-out" }]);
    });
  });

  it("a outra aba põe null no /me ao receber signed-out", async () => {
    const { a, b, client } = twoTabs();
    onTestFinished(watchSignedOutElsewhere(client, b));

    announceSignedOut(a);

    await waitFor(() => {
      expect(client.getQueryData(ME_QUERY_KEY)).toBeNull();
    });
  });

  it("mensagem de outro formato é ignorada", async () => {
    const { a, b, client } = twoTabs();
    onTestFinished(watchSignedOutElsewhere(client, b));

    a?.postMessage({ type: "x" });
    a?.postMessage("signed-out");
    await deliveries();

    expect(client.getQueryData(ME_QUERY_KEY)).toEqual(meBody());
  });

  it("o unsubscribe para de ouvir", async () => {
    const { a, b, client } = twoTabs();
    const unsubscribe = watchSignedOutElsewhere(client, b);

    unsubscribe();
    announceSignedOut(a);
    await deliveries();

    expect(client.getQueryData(ME_QUERY_KEY)).toEqual(meBody());
  });

  it("sem canal, ouvir não faz nada e o unsubscribe não lança", () => {
    const client = new QueryClient();

    expect(() => {
      watchSignedOutElsewhere(client, null)();
    }).not.toThrow();
  });

  it("canal já fechado: avisar não lança, para o logout seguir", () => {
    const channel = openSessionChannel(fakeChannelNetwork());
    channel?.close();

    expect(() => {
      announceSignedOut(channel);
    }).not.toThrow();
  });
});
