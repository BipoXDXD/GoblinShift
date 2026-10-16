// A conta da API falsa do E2E: sessão por cookie com CSRF, aceite dos termos, chaves de API e
// exclusão, com as mesmas regras da API real (dependencies.py, cookies.py, auth_login.py,
// auth_session.py, api_keys.py, me.py, me_delete.py e me_terms.py). O login pelo provedor é a
// única coisa simulada: GET /auth/login/{provider} cria a sessão na hora e volta para o
// `return_to`, como o callback faria.
import { randomUUID } from "node:crypto";

import type { BrowserContext, Request, Route } from "@playwright/test";
import { z } from "zod";

import type { components } from "../../src/api/schema";

type Schemas = components["schemas"];
type ProblemCode = Schemas["ProblemCode"];

const SESSION_COOKIE = "__Host-gs_session";
const CSRF_COOKIE = "__Host-gs_csrf";
const CSRF_HEADER = "x-csrf-token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const TERMS_VERSION = "v2-rascunho";
const MAX_ACTIVE_KEYS = 5;
const NOW = "2026-10-14T12:00:00Z";
// Os mesmos de auth_login.py: só caminho local, até 512 caracteres.
const RETURN_TO_PATTERN = /^\/(?!\/)[A-Za-z0-9/_\-.?=&%]*$/;
const MAX_RETURN_TO_CHARS = 512;
const LOGIN_FAILED_LOCATION = "/?login_error=failed";
// As rotas que a conta sem o aceite continua usando (_TERMS_EXEMPT_ROUTES).
const TERMS_EXEMPT = new Set([
  "GET /me",
  "POST /me/terms:accept",
  "DELETE /me",
  "GET /api-keys",
  "DELETE /api-keys/{id}",
]);

const createKeySchema = z
  .object({
    scopes: z
      .array(z.enum(["files:read", "files:write", "runs:read", "runs:write"]))
      .min(1)
      .max(4),
    days: z.number().int().min(1).max(90).default(30),
  })
  .strict();
const acceptTermsSchema = z.object({ version: z.string().min(1).max(64) }).strict();

/** Como a sessão nasce no login simulado. */
export interface SessionOptions {
  /** A conta já aceitou a versão vigente dos termos. */
  readonly termsAccepted?: boolean;
  /** Login no provedor há menos de 10 min (criar chave e excluir a conta exigem). */
  readonly recentAuth?: boolean;
}

export type Principal = { readonly via: "session" | "api_key" } | null;

interface Session {
  readonly token: string;
  readonly csrf: string;
  recentAuth: boolean;
}

interface StoredKey {
  key: Schemas["ApiKeyResponse"];
}

/** Resposta de problema já decidida, para o chamador mandar. */
export interface Refusal {
  readonly status: number;
  readonly code: ProblemCode;
  readonly headers?: Record<string, string>;
}

export class FakeAccount {
  readonly #context: BrowserContext;
  readonly #apiKey: string;
  #session: Session | null = null;
  /** A conta aceitou a versão vigente dos termos (vale para os próximos logins). */
  termsAccepted = true;
  #deleted = false;
  /** A chave do processo de teste vale; `false` simula o navegador sem credencial nenhuma. */
  apiKeyValid = true;
  /** O próximo login no provedor é recusado (conta bloqueada, `state` inválido…). */
  refuseNextLogin = false;
  /** Código do último login recusado: o `X-Request-ID` do callback, como na API real. */
  lastLoginRef: string | null = null;
  /** O próximo login no provedor nasce sem autenticação recente. */
  nextLoginRecent = true;
  readonly keys = new Map<string, StoredKey>();
  /** Segredo de cada chave criada, para a spec procurar vazamento. */
  readonly issuedTokens: string[] = [];
  /** Mutações recusadas por CSRF, na ordem. */
  readonly csrfFailures: string[] = [];

  constructor(context: BrowserContext, apiKey: string) {
    this.#context = context;
    this.#apiKey = apiKey;
  }

  get deleted(): boolean {
    return this.#deleted;
  }

  /** Sessão pronta no contexto, como depois do callback do login. */
  async signIn(origin: string, options: SessionOptions = {}): Promise<void> {
    this.termsAccepted = options.termsAccepted ?? true;
    await this.#startSession(origin, options.recentAuth ?? true);
  }

  /** Chave já existente na conta, para as telas da matriz; devolve o id público. */
  seedKey(overrides: Partial<Omit<Schemas["ApiKeyResponse"], "id">> = {}): string {
    const id = randomUUID();
    const key: Schemas["ApiKeyResponse"] = {
      id,
      public_id: `gsk_${id.slice(0, 8)}`,
      scopes: ["runs:read", "runs:write"],
      created_at: "2026-10-01T12:00:00Z",
      expires_at: "2026-10-31T12:00:00Z",
      last_used_at: null,
      revoked_at: null,
      ...overrides,
    };
    this.keys.set(id, { key });
    return key.public_id;
  }

  /**
   * Quem fez o pedido: a sessão do cookie ou a chave do Authorization (as duas juntas → 400).
   * Mutação pela sessão sem o header de CSRF igual ao cookie → 403 CSRF_FAILED.
   */
  async identify(request: Request): Promise<Principal | Refusal> {
    const cookies = parseCookies(await request.headerValue("cookie"));
    const authorization = await request.headerValue("authorization");
    const sessionToken = cookies.get(SESSION_COOKIE);
    if (sessionToken !== undefined && authorization !== null) {
      return { status: 400, code: "BAD_REQUEST" };
    }
    if (sessionToken !== undefined) {
      if (this.#session === null || sessionToken !== this.#session.token) {
        return unauthenticated();
      }
      if (!SAFE_METHODS.has(request.method())) {
        const header = await request.headerValue(CSRF_HEADER);
        if (header === null || header !== cookies.get(CSRF_COOKIE)) {
          this.csrfFailures.push(`${request.method()} ${new URL(request.url()).pathname}`);
          return { status: 403, code: "CSRF_FAILED" };
        }
      }
      return { via: "session" };
    }
    if (authorization === `Bearer ${this.#apiKey}` && this.apiKeyValid && !this.#deleted) {
      return { via: "api_key" };
    }
    return unauthenticated();
  }

  /** O bloqueio da autenticação: sem o aceite, só as rotas da allowlist. */
  termsRefusal(principal: Principal, route: string): Refusal | null {
    if (principal?.via !== "session" || this.termsAccepted || TERMS_EXEMPT.has(route)) {
      return null;
    }
    return { status: 403, code: "TERMS_NOT_ACCEPTED" };
  }

  /** As rotas da conta em /api/v1; `false` quando a rota não é da conta. */
  async handle(route: Route, principal: Principal, template: string): Promise<boolean> {
    const request = route.request();
    switch (template) {
      case "GET /me":
        await fulfillJson(route, this.#me(principal), 200);
        return true;
      case "POST /me/terms:accept":
        await this.#acceptTerms(route, principal, request);
        return true;
      case "DELETE /me":
        await this.#deleteAccount(route, principal);
        return true;
      case "GET /api-keys":
        await this.#listKeys(route, principal);
        return true;
      case "POST /api-keys":
        await this.#createKey(route, principal, request);
        return true;
      case "DELETE /api-keys/{id}":
        await this.#revokeKey(route, principal, request);
        return true;
      default:
        return false;
    }
  }

  /** /auth/*: o login simulado, o logout e o logout-all. */
  async handleAuth(route: Route): Promise<boolean> {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const [, , action, provider] = url.pathname.split("/");
    if (
      method === "GET" &&
      action === "login" &&
      (provider === "google" || provider === "github")
    ) {
      await this.#login(route, url);
      return true;
    }
    if (method === "POST" && url.pathname === "/auth/logout") {
      const principal = await this.identify(request);
      if (isRefusal(principal) && principal.code === "CSRF_FAILED") {
        await fulfillProblem(route, principal);
        return true;
      }
      // Sem sessão válida, como na API: só com o par cookie e header de CSRF.
      const withSession = !isRefusal(principal) && principal?.via === "session";
      if (!withSession && !(await csrfPairMatches(request))) {
        this.csrfFailures.push("POST /auth/logout");
        await fulfillProblem(route, { status: 403, code: "CSRF_FAILED" });
        return true;
      }
      await this.#signOut(route);
      return true;
    }
    if (method === "POST" && url.pathname === "/auth/logout-all") {
      const principal = await this.identify(request);
      if (isRefusal(principal)) {
        await fulfillProblem(route, principal);
        return true;
      }
      if (principal?.via !== "session") {
        await fulfillProblem(route, { status: 403, code: "FORBIDDEN" });
        return true;
      }
      for (const stored of this.keys.values()) {
        stored.key = { ...stored.key, revoked_at: stored.key.revoked_at ?? NOW };
      }
      await this.#signOut(route);
      return true;
    }
    return false;
  }

  async #login(route: Route, url: URL): Promise<void> {
    if (this.refuseNextLogin || this.#deleted) {
      this.refuseNextLogin = false;
      const ref = randomUUID();
      this.lastLoginRef = ref;
      await route.fulfill({
        status: 303,
        headers: { Location: `${LOGIN_FAILED_LOCATION}#ref=${ref}`, "X-Request-ID": ref },
      });
      return;
    }
    await this.#startSession(url.origin, this.nextLoginRecent);
    const returnTo = url.searchParams.get("return_to") ?? "/";
    const safe =
      returnTo.length <= MAX_RETURN_TO_CHARS && RETURN_TO_PATTERN.test(returnTo) ? returnTo : "/";
    await redirect(route, safe);
  }

  async #startSession(origin: string, recentAuth: boolean): Promise<void> {
    const session: Session = { token: randomUUID(), csrf: randomUUID(), recentAuth };
    this.#session = session;
    // Os atributos de cookies.py: __Host- exige Secure, Path=/ e nenhum Domain. O Chromium trata
    // http://127.0.0.1 como contexto seguro; o cookie com `domain` sem ponto é host-only, e só
    // assim o Playwright aceita Secure fora do HTTPS (com `url` ele recusa).
    const host = { domain: new URL(origin).hostname, path: "/", secure: true } as const;
    await this.#context.addCookies([
      { name: SESSION_COOKIE, value: session.token, ...host, httpOnly: true, sameSite: "Lax" },
      { name: CSRF_COOKIE, value: session.csrf, ...host, httpOnly: false, sameSite: "Strict" },
    ]);
  }

  async #signOut(route: Route): Promise<void> {
    this.#session = null;
    await this.#context.clearCookies();
    await route.fulfill({ status: 204, headers: { "Clear-Site-Data": '"cookies", "storage"' } });
  }

  #me(principal: Principal): Schemas["MeResponse"] {
    const viaSession = principal?.via === "session";
    const accepted = !viaSession || this.termsAccepted;
    return {
      id: "5f0c7c1a-2b3d-4e5f-8a9b-0c1d2e3f4a5b",
      display_name: "Ana Teste",
      scopes: ["files:read", "files:write", "runs:read", "runs:write"],
      via: viaSession ? "session" : "api_key",
      limits: {
        runs_per_24h: 30,
        input_bytes_per_24h: 2_147_483_648,
        storage_bytes: 4_294_967_296,
        active_runs: 1,
        file_bytes: 268_435_456,
        batch_files: 50,
        batch_bytes: 536_870_912,
        concurrent_uploads: 4,
        concurrent_downloads: 4,
      },
      usage: {
        runs_last_24h: 3,
        input_bytes_last_24h: 12_500_000,
        storage_reserved_bytes: 40_000_000,
        active_run_id: null,
        daily_quota_resets_at: "2026-10-15T09:30:00Z",
      },
      terms: {
        current_version: TERMS_VERSION,
        accepted_version: accepted ? TERMS_VERSION : null,
        accepted_at: accepted ? NOW : null,
        acceptance_required: !accepted,
      },
    };
  }

  async #acceptTerms(route: Route, principal: Principal, request: Request): Promise<void> {
    if (principal?.via !== "session") {
      await fulfillProblem(route, { status: 403, code: "FORBIDDEN" });
      return;
    }
    const body = acceptTermsSchema.safeParse(request.postDataJSON());
    if (!body.success) {
      await fulfillProblem(route, { status: 422, code: "VALIDATION_FAILED" });
      return;
    }
    if (body.data.version !== TERMS_VERSION) {
      await fulfillProblem(route, { status: 409, code: "STATE_CONFLICT" });
      return;
    }
    this.termsAccepted = true;
    const accepted: Schemas["AcceptedTermsResponse"] = { version: TERMS_VERSION, accepted_at: NOW };
    await fulfillJson(route, accepted, 200);
  }

  async #deleteAccount(route: Route, principal: Principal): Promise<void> {
    const refusal = this.#recentSessionRefusal(principal);
    if (refusal !== null) {
      await fulfillProblem(route, refusal);
      return;
    }
    this.#deleted = true;
    this.#session = null;
    await this.#context.clearCookies();
    await route.fulfill({ status: 202, headers: { "Clear-Site-Data": '"cookies", "storage"' } });
  }

  async #listKeys(route: Route, principal: Principal): Promise<void> {
    if (principal?.via !== "session") {
      await fulfillProblem(route, { status: 403, code: "FORBIDDEN" });
      return;
    }
    const data = [...this.keys.values()].map((stored) => stored.key);
    await fulfillJson(route, { data } satisfies Schemas["ApiKeyList"], 200);
  }

  async #createKey(route: Route, principal: Principal, request: Request): Promise<void> {
    const refusal = this.#recentSessionRefusal(principal);
    if (refusal !== null) {
      await fulfillProblem(route, refusal);
      return;
    }
    const body = createKeySchema.safeParse(request.postDataJSON());
    if (!body.success) {
      await fulfillProblem(route, { status: 422, code: "VALIDATION_FAILED" });
      return;
    }
    const active = [...this.keys.values()].filter((stored) => stored.key.revoked_at === null);
    if (active.length >= MAX_ACTIVE_KEYS) {
      await fulfillProblem(route, { status: 409, code: "STATE_CONFLICT" });
      return;
    }
    const id = randomUUID();
    const publicId = `gsk_${id.slice(0, 8)}`;
    const token = `${publicId}.CANARY-${randomUUID()}`;
    const expiresAt = new Date(Date.parse(NOW) + body.data.days * 86_400_000).toISOString();
    this.issuedTokens.push(token);
    this.keys.set(id, {
      key: {
        id,
        public_id: publicId,
        scopes: body.data.scopes,
        created_at: NOW,
        expires_at: expiresAt,
        last_used_at: null,
        revoked_at: null,
      },
    });
    const created: Schemas["CreatedApiKey"] = {
      id,
      public_id: publicId,
      token,
      scopes: body.data.scopes,
      expires_at: expiresAt,
    };
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify(created),
    });
  }

  async #revokeKey(route: Route, principal: Principal, request: Request): Promise<void> {
    if (principal?.via !== "session") {
      await fulfillProblem(route, { status: 403, code: "FORBIDDEN" });
      return;
    }
    const id = new URL(request.url()).pathname.split("/").at(-1) ?? "";
    const stored = this.keys.get(id);
    if (stored === undefined) {
      await fulfillProblem(route, { status: 404, code: "NOT_FOUND" });
      return;
    }
    stored.key = { ...stored.key, revoked_at: stored.key.revoked_at ?? NOW };
    await route.fulfill({ status: 204 });
  }

  #recentSessionRefusal(principal: Principal): Refusal | null {
    if (principal?.via !== "session") {
      return { status: 403, code: "FORBIDDEN" };
    }
    if (this.#session?.recentAuth !== true) {
      return { status: 401, code: "REAUTH_REQUIRED" };
    }
    return null;
  }
}

export function isRefusal(value: Principal | Refusal): value is Refusal {
  return value !== null && "code" in value;
}

function unauthenticated(): Refusal {
  return {
    status: 401,
    code: "AUTHENTICATION_REQUIRED",
    headers: { "WWW-Authenticate": "Bearer" },
  };
}

/** Header de CSRF presente e igual ao cookie: o que a API exige de um logout sem sessão. */
async function csrfPairMatches(request: Request): Promise<boolean> {
  const cookie = parseCookies(await request.headerValue("cookie")).get(CSRF_COOKIE);
  const header = await request.headerValue(CSRF_HEADER);
  return cookie !== undefined && header === cookie;
}

function parseCookies(header: string | null): Map<string, string> {
  const cookies = new Map<string, string>();
  for (const pair of (header ?? "").split(";")) {
    const [name, ...value] = pair.trim().split("=");
    if (name !== undefined && name !== "") {
      cookies.set(name, value.join("="));
    }
  }
  return cookies;
}

async function redirect(route: Route, location: string): Promise<void> {
  await route.fulfill({ status: 303, headers: { Location: location } });
}

async function fulfillJson(route: Route, body: unknown, status: number): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** problem+json no formato da API, usado também pelo resto da API falsa. */
export async function fulfillProblem(route: Route, refusal: Refusal): Promise<void> {
  const problem: Schemas["ProblemDetails"] = {
    type: `https://goblinshift.dev/problems/${refusal.code.toLowerCase().replaceAll("_", "-")}`,
    title: refusal.code,
    status: refusal.status,
    detail: `API falsa do E2E: ${refusal.code}`,
    instance: new URL(route.request().url()).pathname,
    code: refusal.code,
    request_id: randomUUID(),
  };
  await route.fulfill({
    status: refusal.status,
    contentType: "application/problem+json",
    headers: refusal.headers ?? {},
    body: JSON.stringify(problem),
  });
}
