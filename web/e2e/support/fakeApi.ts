// API falsa do E2E local, dentro do processo do Playwright (context.route). Fala o contrato da
// spec (tipos de src/api/schema.d.ts) e guarda estado por contexto: uploads, execuções que andam a
// cada consulta e artefatos para baixar. Só existe no modo local; com E2E_BASE_URL a suíte usa a
// API real (support/env.ts).
import { createHash, randomUUID } from "node:crypto";

import type { BrowserContext, Request, Route } from "@playwright/test";
import { z } from "zod";

import type { components } from "../../src/api/schema";
import { CATALOG_BODY } from "../../src/test/catalogBody";
import { writeArtifact } from "./artifacts";
import { E2E } from "./env";
import {
  FakeAccount,
  fulfillProblem as fulfillRefusal,
  isRefusal,
  type SessionOptions,
} from "./fakeAccount";

type Schemas = components["schemas"];
type Run = Schemas["RunResponse"];
type ArtifactReference = Schemas["ResultReferenceResponse"];

// O pedido sai do bundle, que monta o corpo pela spec; a API falsa lê só o que usa, e um corpo
// fora desse formato derruba o teste com o erro do Zod em vez de seguir com lixo.
const uploadIntentsSchema = z.object({
  items: z.array(z.object({ name: z.string(), size: z.number().int() })),
});
const runCommandSchema = z.object({
  recipe_id: z.string(),
  recipe_version: z.number().int(),
  parameters: z.record(z.string(), z.unknown()),
  input_upload_ids: z.array(z.string()),
});

// GET /runs: os parâmetros que a interface manda, com os limites da spec (1 a 100 por página).
const runListQuerySchema = z
  .object({
    limit: z.coerce.number().int().min(1).max(100).default(25),
    recipe_id: z.string().max(64).optional(),
    status: z.string().optional(),
    created_from: z.iso.datetime({ offset: true }).optional(),
    cursor: z.string().max(1024).optional(),
  })
  .strict();
type RunListQuery = z.infer<typeof runListQuerySchema>;

/** O que a API falsa guarda de cada POST /runs aceito. */
export type RunCommand = z.infer<typeof runCommandSchema>;

/** Execução com os campos que a matriz de estados troca (status, progresso, erro…). */
type RunDetail = Schemas["RunDetailResponse"];
type RunDetailParts = Pick<RunDetail, "summary" | "items">;

/** Execução com os campos que a matriz de estados troca (status, progresso, erro, itens…). */
export type RunOverrides = Partial<Omit<Run, "id"> & RunDetailParts>;

export interface FakeApi {
  /** Corpo de cada POST /runs aceito, na ordem. */
  readonly createdRuns: readonly RunCommand[];
  /** Pedido que nenhuma rota da API falsa reconhece; o fixture exige lista vazia ao fim. */
  readonly unhandled: readonly string[];
  /** Nome do upload pelo id, para conferir a ordem das entradas de uma execução. */
  uploadName(uploadId: string): string | undefined;
  /** Execução parada num estado, para as telas da matriz; devolve o id. */
  seedRun(overrides: RunOverrides): string;
  /** Sessão, termos e chaves da conta (fakeAccount.ts). */
  readonly account: FakeAccount;
  /**
   * Entra com sessão no contexto, como depois do callback do login: com a sessão, a chave do
   * processo de teste deixa de ir (support/apiKey.ts).
   */
  signIn(options?: SessionOptions): Promise<void>;
}

const API_PREFIX = "/api/v1";
// Mesmo intervalo de consulta que a API real devolve (runs.py, POLL_RETRY_AFTER_SECONDS).
const POLL_RETRY_AFTER_SECONDS = "2";
const NOW = "2026-10-14T12:00:00Z";
const LATER = "2026-10-15T12:00:00Z";
// ZIP vazio válido: só o registro de fim do diretório central.
const EMPTY_ZIP = Buffer.from([0x50, 0x4b, 0x05, 0x06, ...new Array<number>(18).fill(0)]);

interface StoredUpload {
  readonly name: string;
  readonly size: number;
  content: Buffer | null;
}

interface StoredRun {
  run: Run;
  /** Resumo e itens da semeada; a criada por POST /runs os deriva das entradas. */
  readonly detail?: RunDetailParts;
  /** Execução criada por POST /runs anda a cada consulta; a semeada fica parada. */
  readonly advances: boolean;
  readonly inputIds: readonly string[];
}

/**
 * Liga a API falsa em todas as páginas do contexto. Pedido sem sessão nem
 * `Authorization: Bearer <apiKey>` recebe 401 AUTHENTICATION_REQUIRED, como na API real: é assim
 * que a suíte prova que a chave só chega pelo processo de teste (support/apiKey.ts) e nunca pelo
 * bundle. A sessão, o CSRF e o aceite dos termos seguem as regras da API (fakeAccount.ts).
 *
 * Os artefatos vão para `artifactsDir`, que o servidor local entrega (support/artifacts.ts).
 */
export async function installFakeApi(
  context: BrowserContext,
  apiKey: string,
  artifactsDir: string,
): Promise<FakeApi> {
  const state = new FakeState(artifactsDir);
  const account = new FakeAccount(context, apiKey);
  await context.route(`**${API_PREFIX}/**`, async (route) => {
    const request = route.request();
    const principal = await account.identify(request);
    if (isRefusal(principal)) {
      await fulfillProblem(route, principal.code, principal);
      return;
    }
    const template = routeTemplate(request);
    const termsRefusal = account.termsRefusal(principal, template);
    if (termsRefusal !== null) {
      await fulfillProblem(route, termsRefusal.code, termsRefusal);
      return;
    }
    if (!(await account.handle(route, principal, template))) {
      await state.handle(route, request);
    }
  });
  await context.route("**/auth/**", async (route) => {
    if (!(await account.handleAuth(route))) {
      const request = route.request();
      state.unhandled.push(`${request.method()} ${new URL(request.url()).pathname}`);
      await fulfillProblem(route, "NOT_FOUND", { status: 404 });
    }
  });
  return {
    createdRuns: state.createdRuns,
    unhandled: state.unhandled,
    uploadName: (uploadId) => state.uploads.get(uploadId)?.name,
    seedRun: (overrides) => state.seedRun(overrides),
    account,
    signIn: (options) => account.signIn(new URL(E2E.baseUrl).origin, options),
  };
}

/** "MÉTODO /caminho" sem o /api/v1, com o id das chaves no lugar do valor. */
function routeTemplate(request: Request): string {
  const path = new URL(request.url()).pathname
    .slice(API_PREFIX.length)
    .replace(/^\/api-keys\/[^/]+$/, "/api-keys/{id}");
  return `${request.method()} ${path}`;
}

class FakeState {
  readonly uploads = new Map<string, StoredUpload>();
  readonly runs = new Map<string, StoredRun>();
  readonly createdRuns: RunCommand[] = [];
  readonly unhandled: string[] = [];
  readonly #artifactsDir: string;

  constructor(artifactsDir: string) {
    this.#artifactsDir = artifactsDir;
  }

  async handle(route: Route, request: Request): Promise<void> {
    const path = new URL(request.url()).pathname.slice(API_PREFIX.length);
    const method = request.method();
    const segments = path.split("/").filter((segment) => segment !== "");
    const [resource, id, action] = segments;

    if (method === "GET" && path === "/catalog") {
      await fulfillJson(route, CATALOG_BODY, { status: 200, headers: { ETag: '"catalogo-e2e"' } });
    } else if (method === "POST" && path === "/uploads") {
      await this.createUploads(route, request);
    } else if (method === "PUT" && resource === "uploads" && action === "content" && id) {
      await this.receiveContent(route, request, id);
    } else if (method === "POST" && path === "/runs") {
      await this.createRun(route, request);
    } else if (method === "GET" && path === "/runs") {
      await this.listRuns(route, request);
    } else if (method === "GET" && resource === "runs" && id && segments.length === 2) {
      await this.pollRun(route, id);
    } else if (method === "GET" && resource === "runs" && id && action === "results") {
      await this.results(route, id);
    } else if (method === "GET" && resource === "artifacts" && id && action === "download") {
      // O Chromium cancela download vindo do route.fulfill: segue para o servidor local, que
      // entrega o que publishArtifacts gravou (e2e/server/serve-dist.ts).
      await route.continue();
    } else {
      this.unhandled.push(`${method} ${path}`);
      await fulfillProblem(route, "NOT_FOUND", { status: 404 });
    }
  }

  seedRun(overrides: RunOverrides): string {
    const id = randomUUID();
    const { summary = null, items = [], ...runOverrides } = overrides;
    const run = { ...runBody(id, "csv-janitor"), ...runOverrides };
    this.runs.set(id, { run, detail: { summary, items }, advances: false, inputIds: [] });
    return id;
  }

  private async createUploads(route: Route, request: Request): Promise<void> {
    const { items } = uploadIntentsSchema.parse(request.postDataJSON());
    const created: Schemas["CreatedUpload"][] = items.map(({ name, size }) => {
      const id = randomUUID();
      this.uploads.set(id, { name, size, content: null });
      return {
        id,
        name,
        size,
        state: "created",
        expires_at: LATER,
        content: {
          method: "PUT",
          url: `${API_PREFIX}/uploads/${id}/content`,
          headers: { "Content-Type": "application/octet-stream" },
        },
      };
    });
    await fulfillJson(route, { items: created } satisfies Schemas["CreatedUploads"], {
      status: 201,
    });
  }

  private async receiveContent(route: Route, request: Request, id: string): Promise<void> {
    const upload = this.uploads.get(id);
    const content = request.postDataBuffer() ?? Buffer.alloc(0);
    if (upload === undefined) {
      await fulfillProblem(route, "NOT_FOUND", { status: 404 });
      return;
    }
    if (content.length !== upload.size) {
      await fulfillProblem(route, "BAD_REQUEST", { status: 400 });
      return;
    }
    upload.content = content;
    const received: Schemas["ReceivedContent"] = {
      id,
      state: "ready",
      size: content.length,
      sha256: createHash("sha256").update(content).digest("hex"),
    };
    await fulfillJson(route, received, { status: 200 });
  }

  private async createRun(route: Route, request: Request): Promise<void> {
    const command = runCommandSchema.parse(request.postDataJSON());
    const inputIds = command.input_upload_ids;
    const allReady = inputIds.every((uploadId) => isReceived(this.uploads.get(uploadId)));
    if ((await request.headerValue("idempotency-key")) === null || !allReady) {
      await fulfillProblem(route, "BAD_REQUEST", { status: 400 });
      return;
    }
    this.createdRuns.push(command);
    const id = randomUUID();
    const run = runBody(id, command.recipe_id);
    this.runs.set(id, { run, advances: true, inputIds });
    const accepted: Schemas["AcceptedRunResponse"] = { ...run, idempotency_expires_at: LATER };
    await fulfillJson(route, accepted, { status: 202 });
  }

  /**
   * As execuções da conta, das mais novas para as mais antigas, com keyset por (created_at, id)
   * como a API real. O cursor guarda o fim da página e os filtros: cursor de outros filtros, ou
   * ilegível, recebe 400, que é o que a interface trata como cursor vencido.
   */
  private async listRuns(route: Route, request: Request): Promise<void> {
    const parsed = runListQuerySchema.safeParse(
      Object.fromEntries(new URL(request.url()).searchParams),
    );
    const cursor = parsed.success ? readCursor(parsed.data) : undefined;
    if (!parsed.success || cursor === null) {
      await fulfillProblem(route, "BAD_REQUEST", { status: 400 });
      return;
    }
    const query = parsed.data;
    const matching = [...this.runs.values()]
      .map((stored) => stored.run)
      .filter((run) => matchesFilters(run, query))
      .sort(newestFirst)
      .filter((run) => cursor === undefined || newestFirst(run, cursor) > 0);
    const data = matching.slice(0, query.limit);
    const last = data.at(-1);
    const next_cursor =
      matching.length > query.limit && last !== undefined ? writeCursor(last, query) : null;
    await fulfillJson(route, { data, next_cursor } satisfies Schemas["RunListResponse"], {
      status: 200,
    });
  }

  /** Fila → em andamento → concluída: uma transição por consulta, como um worker rápido. */
  private async pollRun(route: Route, id: string): Promise<void> {
    const stored = this.runs.get(id);
    if (stored === undefined) {
      await fulfillProblem(route, "NOT_FOUND", { status: 404 });
      return;
    }
    if (stored.advances) {
      stored.run = nextState(stored.run, stored.inputIds.length);
    }
    const headers: Record<string, string> = isTerminal(stored.run)
      ? {}
      : { "Retry-After": POLL_RETRY_AFTER_SECONDS };
    const detail: RunDetail = { ...stored.run, ...(stored.detail ?? derivedDetail(stored)) };
    await fulfillJson(route, detail, { status: 200, headers });
  }

  private async results(route: Route, id: string): Promise<void> {
    const stored = this.runs.get(id);
    if (stored === undefined) {
      await fulfillProblem(route, "NOT_FOUND", { status: 404 });
      return;
    }
    if (!isTerminal(stored.run)) {
      await fulfillProblem(route, "STATE_CONFLICT", { status: 409 });
      return;
    }
    const artifacts = this.publishArtifacts(stored);
    const results: Schemas["RunResultsResponse"] = {
      published: true,
      expires_at: LATER,
      artifacts,
    };
    await fulfillJson(route, results, { status: 200 });
  }

  /** Pacote `<receita>.zip` (nome da API real, packaging/spec.py) e um arquivo por entrada. */
  private publishArtifacts(stored: StoredRun): ArtifactReference[] {
    const recipeId = stored.run.recipe_id;
    const outputs = stored.inputIds.flatMap((uploadId) => {
      const upload = this.uploads.get(uploadId);
      return upload?.content ? [{ name: upload.name, body: upload.content }] : [];
    });
    const files = [
      { role: "package" as const, name: `${recipeId}.zip`, body: EMPTY_ZIP },
      ...outputs.map((output) => ({ role: "output" as const, ...output })),
    ];
    return files.map(({ role, name, body }) => {
      const artifactId = randomUUID();
      writeArtifact(this.#artifactsDir, artifactId, {
        fileName: name,
        body,
        contentType: role === "package" ? "application/zip" : "application/octet-stream",
      });
      return { artifact_id: artifactId, logical_name: name, role, file_name: name };
    });
  }
}

/** Como o worker: itens pendentes enquanto roda; ao concluir, todos ok e o resumo. */
function derivedDetail({ run, inputIds }: StoredRun): RunDetailParts {
  if (run.status === "queued") {
    return { summary: null, items: [] };
  }
  const done = isTerminal(run);
  const items = inputIds.map((uploadId, index) => ({
    position: index + 1,
    upload_id: uploadId,
    status: done ? ("ok" as const) : ("pending" as const),
    error_code: null,
    warnings: [],
    counts: {},
    duplicate_of: null,
  }));
  const count = inputIds.length;
  const summary = done
    ? { inputs: count, ok: count, rejected: 0, duplicates: 0, outputs: count }
    : null;
  return { summary, items };
}

function isReceived(upload: StoredUpload | undefined): boolean {
  return upload !== undefined && upload.content !== null;
}

function matchesFilters(run: Run, query: RunListQuery): boolean {
  return (
    (query.recipe_id === undefined || run.recipe_id === query.recipe_id) &&
    (query.status === undefined || run.status === query.status) &&
    (query.created_from === undefined ||
      Date.parse(run.created_at) >= Date.parse(query.created_from))
  );
}

/** Ordem do GET /runs: created_at decrescente, e o id desempata. */
function newestFirst(a: Pick<Run, "created_at" | "id">, b: Pick<Run, "created_at" | "id">): number {
  const byDate = Date.parse(b.created_at) - Date.parse(a.created_at);
  return byDate !== 0 ? byDate : b.id.localeCompare(a.id);
}

const cursorSchema = z.object({ created_at: z.string(), id: z.string(), filters: z.string() });

function filtersKey({ recipe_id, status, created_from }: RunListQuery): string {
  return JSON.stringify([recipe_id ?? null, status ?? null, created_from ?? null]);
}

function writeCursor(run: Run, query: RunListQuery): string {
  const cursor = { created_at: run.created_at, id: run.id, filters: filtersKey(query) };
  return Buffer.from(JSON.stringify(cursor)).toString("base64url");
}

/** Sem cursor → undefined; cursor ilegível ou de outros filtros → null. */
function readCursor(query: RunListQuery): Pick<Run, "created_at" | "id"> | null | undefined {
  if (query.cursor === undefined) {
    return undefined;
  }
  try {
    const decoded: unknown = JSON.parse(Buffer.from(query.cursor, "base64url").toString("utf8"));
    const cursor = cursorSchema.parse(decoded);
    return cursor.filters === filtersKey(query) ? cursor : null;
  } catch {
    // Cursor forjado ou truncado: a API real também responde 400, sem detalhar.
    return null;
  }
}

function runBody(id: string, recipeId: string): Run {
  return {
    id,
    recipe_id: recipeId,
    recipe_version: 1,
    status: "queued",
    attempt: 1,
    progress: { phase: null, processed: 0, total: 0 },
    queue_ahead: 0,
    created_at: NOW,
    queue_expires_at: LATER,
    deadline_at: null,
    error: null,
    parameters: {},
    preset_id: null,
    results_expire_at: null,
  };
}

function nextState(run: Run, inputs: number): Run {
  if (run.status === "queued") {
    return {
      ...run,
      status: "running",
      deadline_at: LATER,
      progress: { phase: "processing", processed: 0, total: inputs },
    };
  }
  if (run.status === "running") {
    return {
      ...run,
      status: "succeeded",
      progress: { phase: null, processed: inputs, total: inputs },
    };
  }
  return run;
}

const TERMINAL: ReadonlySet<Run["status"]> = new Set([
  "succeeded",
  "succeeded_with_warnings",
  "canceled",
  "failed",
]);

function isTerminal(run: Run): boolean {
  return TERMINAL.has(run.status);
}

interface Reply {
  readonly status: number;
  readonly headers?: Record<string, string>;
}

async function fulfillJson(
  route: Route,
  body: unknown,
  { status, headers = {} }: Reply,
): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers,
    body: JSON.stringify(body),
  });
}

async function fulfillProblem(
  route: Route,
  code: Schemas["ProblemCode"],
  { status, headers = {} }: Reply,
): Promise<void> {
  await fulfillRefusal(route, { status, code, headers });
}
