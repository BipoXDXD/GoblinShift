// Ponto de extensão da fatia 6: o cookie e o header de CSRF entram com o login por sessão. Até lá
// o cookie não existe e nenhuma mutação leva o header.
const CSRF_COOKIE = "__Host-gs_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

/** Lê o token de CSRF; `null` quando não há sessão. */
export type CsrfTokenSource = () => string | null;

export function csrfTokenFromCookies(cookies: string): string | null {
  for (const pair of cookies.split(";")) {
    const [name, ...rest] = pair.trim().split("=");
    if (name === CSRF_COOKIE) {
      return decodeCookieValue(rest.join("="));
    }
  }
  return null;
}

export const readCsrfCookie: CsrfTokenSource = () => csrfTokenFromCookies(document.cookie);

function decodeCookieValue(raw: string): string | null {
  try {
    const value = decodeURIComponent(raw);
    return value === "" ? null : value;
  } catch {
    // Percent-encoding inválido: o cookie não é nosso token, então a mutação vai sem ele e o
    // servidor responde CSRF_FAILED.
    return null;
  }
}
