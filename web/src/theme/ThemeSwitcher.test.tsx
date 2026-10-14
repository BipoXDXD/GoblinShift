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
});
