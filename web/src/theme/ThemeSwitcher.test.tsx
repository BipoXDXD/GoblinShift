import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { axeViolations } from "../test/axe";
import { installFakeMatchMedia } from "../test/fakeMatchMedia";
import { ThemeSwitcher } from "./ThemeSwitcher";

function themeAttribute(): string | undefined {
  return document.documentElement.dataset["theme"];
}

async function openSwitcher(): Promise<void> {
  await userEvent.click(screen.getByText("Tema"));
}

describe("ThemeSwitcher", () => {
  it("começa seguindo o sistema e aplica o tema dele", async () => {
    installFakeMatchMedia(true);
    render(<ThemeSwitcher />);
    await openSwitcher();

    expect(screen.getByRole("radio", { name: "Sistema" })).toBeChecked();
    expect(themeAttribute()).toBe("dark");
  });

  it("escolher Claro fixa o tema e grava a escolha", async () => {
    installFakeMatchMedia(true);
    render(<ThemeSwitcher />);
    await openSwitcher();

    await userEvent.click(screen.getByRole("radio", { name: "Claro" }));

    expect(themeAttribute()).toBe("light");
    expect(window.localStorage.getItem("gs-theme")).toBe("light");
  });

  it("abre com a escolha gravada antes", async () => {
    installFakeMatchMedia(false);
    window.localStorage.setItem("gs-theme", "dark");
    render(<ThemeSwitcher />);
    await openSwitcher();

    expect(screen.getByRole("radio", { name: "Escuro" })).toBeChecked();
    expect(themeAttribute()).toBe("dark");
  });

  it("em Sistema, acompanha a troca de tema do sistema", () => {
    const system = installFakeMatchMedia(false);
    render(<ThemeSwitcher />);

    act(() => {
      system.setSystemDark(true);
    });

    expect(themeAttribute()).toBe("dark");
  });

  it("com o tema fixo, ignora a troca do sistema", async () => {
    const system = installFakeMatchMedia(false);
    render(<ThemeSwitcher />);
    await openSwitcher();
    await userEvent.click(screen.getByRole("radio", { name: "Claro" }));

    act(() => {
      system.setSystemDark(true);
    });

    expect(themeAttribute()).toBe("light");
  });

  it("o resumo diz a escolha atual e o grupo não tem violações do axe", async () => {
    installFakeMatchMedia(false);
    const { container } = render(<ThemeSwitcher />);
    await openSwitcher();
    await userEvent.click(screen.getByRole("radio", { name: "Escuro" }));

    expect(screen.getByText("Escuro", { selector: "summary *" })).toBeInTheDocument();
    expect(await axeViolations(container)).toEqual([]);
  });

  describe("fecha como um menu suspenso (WCAG 2.4.11)", () => {
    function details(): HTMLDetailsElement {
      const summary = screen.getByText("Tema").closest("summary");
      const element = summary?.parentElement;
      if (!(element instanceof HTMLDetailsElement)) {
        throw new Error("o seletor de tema não está num <details>");
      }
      return element;
    }

    function renderBetweenButtons(): void {
      render(
        <>
          <button type="button">Antes</button>
          <ThemeSwitcher />
          <button type="button">Depois</button>
        </>,
      );
    }

    it("Esc fecha e devolve o foco ao botão que abriu", async () => {
      installFakeMatchMedia(false);
      render(<ThemeSwitcher />);
      await openSwitcher();
      screen.getByRole("radio", { name: "Sistema" }).focus();

      await userEvent.keyboard("{Escape}");

      expect(details().open).toBe(false);
      expect(screen.getByText("Tema").closest("summary")).toHaveFocus();
    });

    it("Esc com o seletor fechado não move o foco", async () => {
      installFakeMatchMedia(false);
      renderBetweenButtons();
      screen.getByRole("button", { name: "Antes" }).focus();

      await userEvent.keyboard("{Escape}");

      expect(screen.getByRole("button", { name: "Antes" })).toHaveFocus();
    });

    it("Tab para fora do seletor fecha", async () => {
      installFakeMatchMedia(false);
      renderBetweenButtons();
      await openSwitcher();
      screen.getByRole("radio", { name: "Sistema" }).focus();

      await userEvent.tab();

      expect(screen.getByRole("button", { name: "Depois" })).toHaveFocus();
      expect(details().open).toBe(false);
    });

    it("Shift+Tab do resumo para trás também fecha", async () => {
      installFakeMatchMedia(false);
      renderBetweenButtons();
      await openSwitcher();

      await userEvent.tab({ shift: true });

      expect(screen.getByRole("button", { name: "Antes" })).toHaveFocus();
      expect(details().open).toBe(false);
    });

    it("andar do resumo para as opções mantém aberto", async () => {
      installFakeMatchMedia(false);
      renderBetweenButtons();
      await openSwitcher();

      await userEvent.tab();

      expect(screen.getByRole("radio", { name: "Sistema" })).toHaveFocus();
      expect(details().open).toBe(true);
    });

    it("clicar numa opção escolhe e não fecha antes do clique chegar", async () => {
      installFakeMatchMedia(false);
      renderBetweenButtons();
      await openSwitcher();

      await userEvent.click(screen.getByText("Escuro", { selector: "label" }));

      expect(screen.getByRole("radio", { name: "Escuro" })).toBeChecked();
      expect(details().open).toBe(true);
    });
  });
});
