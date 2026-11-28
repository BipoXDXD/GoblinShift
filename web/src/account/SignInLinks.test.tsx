import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SignInLinks } from "./SignInLinks";

function links(): string[] {
  return screen.getAllByRole("link").map((link) => link.getAttribute("href") ?? "");
}

describe("SignInLinks", () => {
  it("mostra só o provedor da sessão na reautenticação", () => {
    render(<SignInLinks returnTo="/acesso" reauth provider="github" />);

    expect(links()).toEqual(["/auth/login/github?return_to=%2Facesso&reauth=1"]);
    expect(screen.getByRole("link", { name: "Entrar com GitHub" })).toBeVisible();
  });

  it("fora da reautenticação mostra os dois", () => {
    render(<SignInLinks returnTo="/acesso" provider="github" />);

    expect(links()).toEqual([
      "/auth/login/google?return_to=%2Facesso",
      "/auth/login/github?return_to=%2Facesso",
    ]);
  });

  it("na reautenticação sem o provedor conhecido mostra os dois", () => {
    render(<SignInLinks returnTo="/acesso" reauth />);

    expect(links()).toEqual([
      "/auth/login/google?return_to=%2Facesso&reauth=1",
      "/auth/login/github?return_to=%2Facesso&reauth=1",
    ]);
  });
});
