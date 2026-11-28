import { onlineManager } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { axeViolations } from "../test/axe";
import { installAnnounceClock, untilFakeClock } from "../test/announceClock";
import { TEXT } from "../text";
import { ConnectionNotice, RECONNECTED_MS } from "./ConnectionNotice";

const T = TEXT.connection;

afterEach(() => {
  onlineManager.setOnline(true);
});

function goOffline(): void {
  act(() => {
    onlineManager.setOnline(false);
  });
}

function goOnline(): void {
  act(() => {
    onlineManager.setOnline(true);
  });
}

describe("ConnectionNotice", () => {
  it("online desde o início, a região existe e está vazia", () => {
    render(<ConnectionNotice />);
    const region = screen.getByRole("status");
    expect(region).toBeEmptyDOMElement();
    expect(region).toHaveAttribute("aria-live", "polite");
  });

  it("offline mostra o aviso com ícone e texto", () => {
    render(<ConnectionNotice />);
    goOffline();
    const region = screen.getByRole("status");
    expect(region).toHaveTextContent(T.offline);
    expect(region).toHaveTextContent(T.offlineDetail);
    expect(region.firstElementChild).toHaveClass("bg-warning-bg");
    expect(region.querySelector("svg.lucide-wifi-off")).toHaveAttribute("aria-hidden", "true");
  });

  it("voltar mostra 'Conexão de volta.' e some depois de RECONNECTED_MS", async () => {
    render(<ConnectionNotice />);
    installAnnounceClock();
    goOffline();
    goOnline();
    const region = screen.getByRole("status");
    await untilFakeClock(() => {
      expect(region).toHaveTextContent(T.back);
    });
    expect(region).not.toHaveTextContent(T.offline);
    expect(region.firstElementChild).toHaveClass("bg-info-bg");
    expect(region.querySelector("svg.lucide-wifi")).toHaveAttribute("aria-hidden", "true");

    act(() => {
      vi.advanceTimersByTime(RECONNECTED_MS - 1);
    });
    expect(region).toHaveTextContent(T.back);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(region).toBeEmptyDOMElement();
  });

  it("cair de novo antes dos 5 s volta ao aviso de sem conexão", () => {
    render(<ConnectionNotice />);
    installAnnounceClock();
    goOffline();
    goOnline();
    goOffline();
    const region = screen.getByRole("status");
    expect(region).toHaveTextContent(T.offline);
    expect(region).not.toHaveTextContent(T.back);
    act(() => {
      vi.advanceTimersByTime(RECONNECTED_MS);
    });
    expect(region).toHaveTextContent(T.offline);
  });

  it("desmontar durante o 'de volta' não deixa timer pendente", async () => {
    const { unmount } = render(<ConnectionNotice />);
    installAnnounceClock();
    goOffline();
    goOnline();
    await untilFakeClock(() => {
      expect(screen.getByRole("status")).toHaveTextContent(T.back);
    });
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("sem violações de axe offline", async () => {
    const { container } = render(<ConnectionNotice />);
    goOffline();
    expect(await axeViolations(container)).toEqual([]);
  });
});
