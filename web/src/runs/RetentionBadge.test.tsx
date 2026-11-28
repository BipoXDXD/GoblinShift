import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatDateTime } from "../format";
import { axeViolations } from "../test/axe";
import { RetentionBadge } from "./RetentionBadge";

const NOW = Date.parse("2026-10-14T12:00:00Z");
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

function iso(offsetMs: number): string {
  return new Date(NOW + offsetMs).toISOString();
}

describe("RetentionBadge", () => {
  it("diz até quando e em quantas horas, no tom de informação, com o relógio", async () => {
    const expiresAt = iso(47 * HOUR);
    const { container } = render(<RetentionBadge expiresAt={expiresAt} nowMs={NOW} />);

    const badge = screen.getByText(
      `Disponível até ${formatDateTime(expiresAt)} · apagado automaticamente em 47 h`,
    );
    expect(badge).toHaveClass("bg-info-bg", "text-info-fg");
    expect(badge.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(await axeViolations(container)).toEqual([]);
  });

  it("nas últimas 6 h o selo usa o tom de aviso e o mesmo ícone", async () => {
    const expiresAt = iso(5 * HOUR);
    const { container } = render(<RetentionBadge expiresAt={expiresAt} nowMs={NOW} />);

    const badge = screen.getByText(/apagado automaticamente em 5 h$/);
    expect(badge).toHaveClass("bg-warning-bg", "text-warning-fg");
    expect(badge).not.toHaveClass("bg-info-bg");
    expect(badge.querySelector("svg")).toHaveClass("lucide-clock");
    expect(await axeViolations(container)).toEqual([]);
  });

  it("com menos de 1 h, conta em minutos", () => {
    render(<RetentionBadge expiresAt={iso(25 * MINUTE)} nowMs={NOW} />);

    expect(screen.getByText(/apagado automaticamente em 25 min$/)).toBeVisible();
  });

  it("vencido, não mostra nada", () => {
    const { container } = render(<RetentionBadge expiresAt={iso(-MINUTE)} nowMs={NOW} />);

    expect(container).toBeEmptyDOMElement();
  });
});
