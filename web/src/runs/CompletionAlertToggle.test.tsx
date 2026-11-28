import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { installFakeNotification } from "../test/fakeNotification";
import { ALERTS_KEY } from "./alerts";
import { CompletionAlertToggle } from "./CompletionAlertToggle";

describe("CompletionAlertToggle", () => {
  it("pede permissão só no clique e liga o aviso", async () => {
    const { requestPermission } = installFakeNotification("default", "granted");
    render(<CompletionAlertToggle finished={false} onLeave={() => undefined} />);
    const toggle = screen.getByRole("button", { name: "Avisar quando terminar" });
    expect(toggle).not.toHaveAttribute("aria-pressed");
    expect(requestPermission).not.toHaveBeenCalled();

    await userEvent.click(toggle);

    expect(requestPermission).toHaveBeenCalledTimes(1);
    // Rótulo que muda (D3) dispensa o aria-pressed (APG): o nome já diz o estado.
    expect(screen.getByRole("button", { name: "Aviso ligado" })).not.toHaveAttribute(
      "aria-pressed",
    );
    expect(window.localStorage.getItem(ALERTS_KEY)).toBe("on");
  });

  it("desliga o aviso no segundo clique", async () => {
    installFakeNotification("granted");
    window.localStorage.setItem(ALERTS_KEY, "on");
    render(<CompletionAlertToggle finished={false} onLeave={() => undefined} />);

    await userEvent.click(screen.getByRole("button", { name: "Aviso ligado" }));

    expect(screen.getByRole("button", { name: "Avisar quando terminar" })).toBeVisible();
    expect(window.localStorage.getItem(ALERTS_KEY)).toBe("off");
  });

  it("permissão negada mostra o motivo e não escreve no console", async () => {
    installFakeNotification("default", "denied");
    render(<CompletionAlertToggle finished={false} onLeave={() => undefined} />);

    await userEvent.click(screen.getByRole("button", { name: "Avisar quando terminar" }));

    expect(
      await screen.findByText(
        "O navegador bloqueou os avisos deste site. O título da aba ainda muda quando a execução terminar.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Avisar quando terminar" })).toBeVisible();
    expect(window.localStorage.getItem(ALERTS_KEY)).toBeNull();
  });

  it("sem Notification, o botão não aparece", () => {
    vi.stubGlobal("Notification", undefined);
    render(<CompletionAlertToggle finished={false} onLeave={() => undefined} />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("pedido rejeitado pelo navegador mostra o motivo", async () => {
    installFakeNotification("default", "rejects");
    render(<CompletionAlertToggle finished={false} onLeave={() => undefined} />);

    await userEvent.click(screen.getByRole("button", { name: "Avisar quando terminar" }));

    expect(
      await screen.findByText(
        "O navegador bloqueou os avisos deste site. O título da aba ainda muda quando a execução terminar.",
      ),
    ).toBeInTheDocument();
  });

  it("no término, com o foco nele, devolve o foco a quem a tela indicar", () => {
    installFakeNotification("default");
    const onLeave = vi.fn();
    const { rerender } = render(<CompletionAlertToggle finished={false} onLeave={onLeave} />);
    screen.getByRole("button", { name: "Avisar quando terminar" }).focus();

    rerender(<CompletionAlertToggle finished onLeave={onLeave} />);

    expect(screen.queryByRole("button")).toBeNull();
    expect(onLeave).toHaveBeenCalledTimes(1);
  });

  it("no término, sem o foco nele, não mexe no foco", () => {
    installFakeNotification("default");
    const onLeave = vi.fn();
    const { rerender } = render(<CompletionAlertToggle finished={false} onLeave={onLeave} />);

    rerender(<CompletionAlertToggle finished onLeave={onLeave} />);

    expect(onLeave).not.toHaveBeenCalled();
  });
});
