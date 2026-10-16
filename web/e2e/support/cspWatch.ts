// Violações de CSP vistas pelo navegador: usadas pela spec da CSP e pelas jornadas contra a VPS.
import type { Page } from "@playwright/test";

interface Violation {
  readonly directive: string;
  readonly blocked: string;
  readonly sample: string;
}

/** Junta, desde antes do primeiro script, cada `securitypolicyviolation` e erro de CSP no console. */
export async function watchCsp(page: Page): Promise<() => Promise<string[]>> {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && /Content Security Policy/i.test(message.text())) {
      consoleErrors.push(message.text());
    }
  });
  await page.addInitScript(() => {
    const violations: Violation[] = [];
    Object.defineProperty(window, "__cspViolations", { value: violations });
    document.addEventListener("securitypolicyviolation", (event) => {
      violations.push({
        directive: event.effectiveDirective,
        blocked: event.blockedURI,
        sample: event.sample,
      });
    });
  });
  return async () => {
    const violations = await page.evaluate(
      () => (window as unknown as { __cspViolations: Violation[] }).__cspViolations,
    );
    return [
      ...violations.map((v) => `${v.directive}: ${v.blocked} ${v.sample}`.trim()),
      ...consoleErrors,
    ];
  };
}
