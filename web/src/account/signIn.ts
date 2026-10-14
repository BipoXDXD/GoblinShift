// Login pelo servidor (auth_login.py): a interface só navega para /auth/login/{provider}; o
// redirect ao provedor, o PKCE, o `state` e os cookies ficam todos no servidor. Nenhum token
// passa pelo JavaScript.

export const PROVIDERS = ["google", "github"] as const;
export type Provider = (typeof PROVIDERS)[number];

// O mesmo padrão e o mesmo teto do servidor (RETURN_TO_PATTERN, MAX_RETURN_TO_CHARS): o servidor
// também recusa, mas assim a interface nunca monta um link que voltaria para a Oficina.
const RETURN_TO_PATTERN = /^\/(?!\/)[A-Za-z0-9/_\-.?=&%]*$/;
const MAX_RETURN_TO_CHARS = 512;
const DEFAULT_RETURN_TO = "/";
/** Parâmetro com que o servidor volta quando o login falha (LOGIN_FAILED_LOCATION). */
export const LOGIN_ERROR_PARAM = "login_error";

/**
 * Caminho local para voltar depois do login, ou `/`. Tira o `login_error` da falha anterior, para
 * a volta não mostrar de novo o erro de um login que deu certo.
 */
export function safeReturnTo(pathname: string, search = ""): string {
  const params = new URLSearchParams(search);
  params.delete(LOGIN_ERROR_PARAM);
  const query = params.toString();
  const candidate = query === "" ? pathname : `${pathname}?${query}`;
  return candidate.length <= MAX_RETURN_TO_CHARS && RETURN_TO_PATTERN.test(candidate)
    ? candidate
    : DEFAULT_RETURN_TO;
}

interface SignInOptions {
  readonly returnTo: string;
  /** Login de novo no provedor, para as operações que exigem autenticação recente. */
  readonly reauth?: boolean;
}

/** Endereço do login: navegação de página inteira (`<a href>`), nunca `fetch`. */
export function signInHref(
  provider: Provider,
  { returnTo, reauth = false }: SignInOptions,
): string {
  const params = new URLSearchParams({ return_to: returnTo });
  if (reauth) {
    params.set("reauth", "1");
  }
  return `/auth/login/${provider}?${params.toString()}`;
}
