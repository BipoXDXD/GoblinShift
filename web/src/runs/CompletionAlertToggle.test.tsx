import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { installFakeNotification } from "../test/fakeNotification";
import { ALERTS_KEY } from "./alerts";
import { CompletionAlertToggle } from "./CompletionAlertToggle";

describe("CompletionAlertToggle", () => {
  it("pede permissão só no clique e liga o aviso", async () => {
    const { requestPermission } = installFakeNotification("default", "granted");
    render(<CompletionAlertToggle />);
    const toggle = screen.getByRole("button", { name: "Avisar quando terminar" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(requestPermission).not.toHaveBeenCalled();

    await userEvent.click(toggle);

    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Aviso ligado" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(window.localStorage.getItem(ALERTS_KEY)).toBe("on");
  });

  it("desliga o aviso no segundo clique", async () => {
    installFakeNotification("granted");
    window.localStorage.setItem(ALERTS_KEY, "on");
    render(<CompletionAlertToggle />);

    await userEvent.click(screen.getByRole("button", { name: "Aviso ligado" }));

    expect(screen.getByRole("button", { name: "Avisar quando terminar" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(window.localStorage.getItem(ALERTS_KEY)).toBe("off");
  });

  it("permissão negada mostra o motivo e não escreve no console", async () => {
    installFakeNotification("default", "denied");
    render(<CompletionAlertToggle />);

    await userEvent.click(screen.getByRole("button", { name: "Avisar quando terminar" }));

    expect(
      await screen.findByText(
        "O navegador bloqueou os avisos deste site. O título da aba ainda muda quando a execução terminar.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Avisar quando terminar" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(window.localStorage.getItem(ALERTS_KEY)).toBeNull();
  });

  it("sem Notification, o botão não aparece", () => {
    // O jsdom não tem `window.Notification`: é o navegador sem suporte.
    render(<CompletionAlertToggle />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
