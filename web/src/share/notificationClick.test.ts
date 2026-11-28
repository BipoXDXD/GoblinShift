import { describe, expect, it } from "vitest";

import { openNotificationTarget, type NotificationClients } from "./notificationClick";

const ORIGIN = "https://goblinshift.test";

interface FakeClients extends NotificationClients {
  readonly focused: string[];
  readonly opened: string[];
}

function fakeClients(urls: readonly string[]): FakeClients {
  const focused: string[] = [];
  const opened: string[] = [];
  return {
    focused,
    opened,
    matchAll: () =>
      Promise.resolve(
        urls.map((url) => ({
          url,
          focus: () => {
            focused.push(url);
            return Promise.resolve();
          },
        })),
      ),
    openWindow: (url) => {
      opened.push(url);
      return Promise.resolve();
    },
  };
}

describe("clique na notificação", () => {
  it("foca a aba que já mostra a execução", async () => {
    const clients = fakeClients([`${ORIGIN}/historico`, `${ORIGIN}/execucoes/abc`]);

    await openNotificationTarget(clients, { url: "/execucoes/abc" }, ORIGIN);

    expect(clients.focused).toEqual([`${ORIGIN}/execucoes/abc`]);
    expect(clients.opened).toEqual([]);
  });

  it("sem aba da execução, abre data.url", async () => {
    const clients = fakeClients([`${ORIGIN}/historico`]);

    await openNotificationTarget(clients, { url: "/execucoes/abc" }, ORIGIN);

    expect(clients.opened).toEqual([`${ORIGIN}/execucoes/abc`]);
  });

  it.each([
    ["outro site", { url: "https://exemplo.com/x" }],
    ["barra dupla", { url: "//exemplo.com/x" }],
    ["sem url", {}],
    ["dado que não é objeto", "texto"],
  ])("data.url fora deste site abre a Oficina (%s)", async (_caso, data) => {
    const clients = fakeClients([]);

    await openNotificationTarget(clients, data, ORIGIN);

    expect(clients.opened).toEqual([`${ORIGIN}/`]);
  });
});
