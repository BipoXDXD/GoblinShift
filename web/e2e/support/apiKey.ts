import type { BrowserContext } from "@playwright/test";

/**
 * Põe a chave de API em cada pedido para a API, no processo do Playwright (plano da fatia 7, 4.2).
 * O bundle, o storage e os cookies nunca a veem: o navegador manda o pedido sem credencial e o
 * Playwright acrescenta o cabeçalho no caminho, inclusive nos downloads por navegação.
 *
 * Registrada depois da API falsa, roda antes dela (o Playwright chama a última rota primeiro) e
 * passa o pedido adiante com `fallback`: para a API falsa no modo local e para a rede no remoto.
 *
 * Com a sessão no cookie (a spec entrou pelo login), a chave fica de fora: a API recusa as duas
 * credenciais juntas, e um navegador de verdade só tem uma.
 */
export async function injectApiKey(
  context: BrowserContext,
  apiKey: string,
  baseUrl: string,
): Promise<void> {
  // A interface só consulta a conta (GET /me) com o cookie legível de CSRF, o sinal de sessão
  // (useMe.ts). O navegador com a chave não tem sessão; o marcador faz a interface agir como
  // navegador autenticado. Não é credencial: a API ignora o CSRF de quem vem com a chave.
  // `domain` sem ponto é host-only, como o __Host- exige; com `url` o Playwright recusa Secure
  // fora do HTTPS (fakeAccount.ts).
  await context.addCookies([
    {
      name: "__Host-gs_csrf",
      value: "e2e-autenticado-pela-chave",
      domain: new URL(baseUrl).hostname,
      path: "/",
      secure: true,
      sameSite: "Strict",
    },
  ]);
  await context.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const cookies = (await request.headerValue("cookie")) ?? "";
    if (SESSION_COOKIE.test(cookies)) {
      await route.fallback();
      return;
    }
    const headers = { ...request.headers(), authorization: `Bearer ${apiKey}` };
    await route.fallback({ headers });
  });
}

const SESSION_COOKIE = /(?:^|;\s*)__Host-gs_session=/;
