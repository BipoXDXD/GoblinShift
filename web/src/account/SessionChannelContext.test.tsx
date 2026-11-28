import { QueryClient } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it } from "vitest";

import { meBody } from "../test/account";
import { fakeChannelNetwork } from "../test/fakeBroadcastChannel";
import { ME_QUERY_KEY } from "./api";
import { announceSignedOut, openSessionChannel } from "./sessionSignal";
import { useOpenSessionChannel } from "./SessionChannelContext";

/** A aba do app (o hook) e outra aba da mesma origem, que avisa. */
function tabs(strict = false) {
  const create = fakeChannelNetwork();
  const other = openSessionChannel(create);
  const client = new QueryClient();
  client.setQueryData(ME_QUERY_KEY, meBody());
  const view = renderHook(() => useOpenSessionChannel(client, create), {
    wrapper: strict ? StrictMode : undefined,
  });
  return { create, other, client, view };
}

describe("useOpenSessionChannel", () => {
  it("abre o canal e põe null no /me quando outra aba sai", async () => {
    const { other, client, view } = tabs();

    await waitFor(() => {
      expect(view.result.current).not.toBeNull();
    });
    announceSignedOut(other);

    await waitFor(() => {
      expect(client.getQueryData(ME_QUERY_KEY)).toBeNull();
    });
  });

  it("fecha o canal no unmount", async () => {
    const { create, view } = tabs();
    await waitFor(() => {
      expect(view.result.current).not.toBeNull();
    });

    view.unmount();

    expect(create.opened.slice(1).map((channel) => channel.closed)).toEqual([true]);
  });

  it("no Strict Mode, fica aberto um canal só, o que as telas recebem, e ele ouve", async () => {
    const { create, other, client, view } = tabs(true);
    await waitFor(() => {
      expect(view.result.current).not.toBeNull();
    });

    const open = create.opened.slice(1).filter((channel) => !channel.closed);
    expect(open).toHaveLength(1);
    expect(view.result.current).toBe(open[0]);
    announceSignedOut(other);
    await waitFor(() => {
      expect(client.getQueryData(ME_QUERY_KEY)).toBeNull();
    });
  });

  it("sem BroadcastChannel, fica sem canal e não quebra", () => {
    const client = new QueryClient();

    const view = renderHook(() =>
      useOpenSessionChannel(client, () => {
        throw new Error("sem BroadcastChannel");
      }),
    );

    expect(view.result.current).toBeNull();
    view.unmount();
  });
});
