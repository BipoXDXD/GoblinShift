import axe from "axe-core";

/**
 * Violações do axe no trecho renderizado, como `id: ajuda` para a mensagem do teste apontar a regra.
 *
 * Sem `color-contrast`: o jsdom não calcula estilos nem desenha, e o contraste entra com o tema
 * (D2, D3), medido no navegador. Sem `region`: os testes montam componentes soltos, fora de landmark.
 */
export async function axeViolations(container: Element): Promise<readonly string[]> {
  const results = await axe.run(container, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  return results.violations.map((violation) => `${violation.id}: ${violation.help}`);
}
