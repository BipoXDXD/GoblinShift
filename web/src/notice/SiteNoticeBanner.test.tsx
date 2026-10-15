import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { axeViolations } from "../test/axe";
import type { NoticeKind, SiteNotice } from "./notice";
import { SiteNoticeBanner } from "./SiteNoticeBanner";

const FROM = Date.parse("2026-10-15T00:00:00Z");
const UNTIL = Date.parse("2026-11-01T00:00:00Z");
const INSIDE = FROM + 1;

function notice(kind: NoticeKind, overrides: Partial<SiteNotice> = {}): SiteNotice {
  return {
    id: `aviso-${kind}`,
    kind,
    title: "Título do aviso",
    body: "Corpo do aviso.",
    link: { href: "/termos", label: "Ler os termos" },
    fromMs: FROM,
    untilMs: UNTIL,
    ...overrides,
  };
}

const browserStorage = (): Storage => window.localStorage;

interface Options {
  readonly now?: number;
  readonly storage?: () => Pick<Storage, "getItem" | "setItem">;
}

function banner(
  value: SiteNotice | null,
  { now = INSIDE, storage = browserStorage }: Options = {},
) {
  const element: ReactElement = (
    <SiteNoticeBanner notice={value} now={() => now} storage={storage} />
  );
  const router = createMemoryRouter([{ path: "/", element }]);
  return render(<RouterProvider router={router} />);
}

const KINDS: readonly NoticeKind[] = ["incident", "shutdown", "terms-change"];

/** O tipo escrito na faixa: o aviso nunca depende só da cor. */
const KIND_LABELS: Record<NoticeKind, string> = {
  incident: "Incidente de segurança",
  shutdown: "Encerramento do serviço",
  "terms-change": "Mudança nos termos",
};

const REGION_NAMES: Record<NoticeKind, string> = {
  incident: "Aviso: incidente de segurança",
  shutdown: "Aviso: encerramento do serviço",
  "terms-change": "Aviso: mudança nos termos",
};

describe("SiteNoticeBanner: quando aparece", () => {
  it("sem aviso, não mostra nada", () => {
    const { container } = banner(null);

    expect(container).toBeEmptyDOMElement();
  });

  it.each([
    ["antes de from", FROM - 1],
    ["em until", UNTIL],
  ])("%s, não mostra nada", (_case, now) => {
    const { container } = banner(notice("incident"), { now });

    expect(container).toBeEmptyDOMElement();
  });

  it.each(KINDS)("%s na janela: região com nome, tipo escrito, título, corpo e link", (kind) => {
    banner(notice(kind));

    const region = screen.getByRole("region", { name: REGION_NAMES[kind] });
    expect(region).toHaveTextContent(KIND_LABELS[kind]);
    expect(region).toHaveTextContent("Título do aviso");
    expect(region).toHaveTextContent("Corpo do aviso.");
    expect(screen.getByRole("link", { name: "Ler os termos" })).toHaveAttribute("href", "/termos");
  });

  it("sem link, não mostra link", () => {
    banner(notice("shutdown", { link: null }));

    expect(screen.queryByRole("link")).toBeNull();
  });

  it("não rouba o foco nem interrompe o leitor de tela", () => {
    banner(notice("incident"));

    expect(document.body).toHaveFocus();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("SiteNoticeBanner: Fechar", () => {
  it.each(["incident", "shutdown"] as const)("%s não tem Fechar", (kind) => {
    banner(notice(kind));

    expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull();
  });

  it("terms-change fecha e guarda o id", async () => {
    banner(notice("terms-change"));

    await userEvent.click(screen.getByRole("button", { name: "Fechar" }));

    expect(screen.queryByRole("region")).toBeNull();
    expect(window.localStorage.getItem("gs-notice-dismissed")).toBe("aviso-terms-change");
  });

  it("fechado continua fechado na próxima carga", async () => {
    const first = banner(notice("terms-change"));
    await userEvent.click(screen.getByRole("button", { name: "Fechar" }));
    first.unmount();

    const { container } = banner(notice("terms-change"));

    expect(container).toBeEmptyDOMElement();
  });

  it("outro aviso de mudança, com id novo, aparece mesmo depois de fechar o anterior", () => {
    window.localStorage.setItem("gs-notice-dismissed", "termos-v1");

    banner(notice("terms-change", { id: "termos-v2" }));

    expect(screen.getByRole("region", { name: REGION_NAMES["terms-change"] })).toBeVisible();
  });

  it("um incidente com o id fechado antes continua aparecendo", () => {
    window.localStorage.setItem("gs-notice-dismissed", "aviso-incident");

    banner(notice("incident"));

    expect(screen.getByRole("region", { name: REGION_NAMES.incident })).toBeVisible();
  });

  it("sem armazenamento, Fechar esconde o aviso mesmo assim", async () => {
    const blocked = (): Storage => {
      throw new DOMException("bloqueado", "SecurityError");
    };
    banner(notice("terms-change"), { storage: blocked });

    await userEvent.click(screen.getByRole("button", { name: "Fechar" }));

    expect(screen.queryByRole("region")).toBeNull();
  });
});

describe("SiteNoticeBanner: acessibilidade", () => {
  it.each(KINDS)("%s não tem violações do axe", async (kind) => {
    const { container } = banner(notice(kind));

    expect(await axeViolations(container)).toEqual([]);
  });
});
