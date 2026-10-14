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

/** O que a API falsa guarda de cada POST /runs aceito. */
export type RunCommand = z.infer<typeof runCommandSchema>;

/** Execução com os campos que a matriz de estados troca (status, progresso, erro…). */
export type RunOverrides = Partial<Omit<Run, "id">>;

export interface FakeApi {
  /** Corpo de cada POST /runs aceito, na ordem. */
  readonly createdRuns: readonly RunCommand[];
  /** Pedido que nenhuma rota da API falsa reconhece; o fixture exige lista vazia ao fim. */
  readonly unhandled: readonly string[];
  /** Nome do upload pelo id, para conferir a ordem das entradas de uma execução. */
  uploadName(uploadId: string): string | undefined;
  /** Execução parada num estado, para as telas da matriz; devolve o id. */
  seedRun(overrides: RunOverrides): string;
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
  /** Execução criada por POST /runs anda a cada consulta; a semeada fica parada. */
  readonly advances: boolean;
  readonly inputIds: readonly string[];
}

/**
 * Liga a API falsa em todas as páginas do contexto. Pedido sem `Authorization: Bearer <apiKey>`
 * recebe 401 AUTHENTICATION_REQUIRED, como na API real: é assim que a suíte prova que a chave só
 * chega pelo processo de teste (support/apiKey.ts) e nunca pelo bundle.
 *
 * Os artefatos vão para `artifactsDir`, que o servidor local entrega (support/artifacts.ts).
 */
export async function installFakeApi(
  context: BrowserContext,
  apiKey: string,
  artifactsDir: string,
): Promise<FakeApi> {
  const state = new FakeState(artifactsDir);
  await context.route(`**${API_PREFIX}/**`, async (route) => {
    const request = route.request();
    const authorization = await request.headerValue("authorization");
    if (authorization !== `Bearer ${apiKey}`) {
      await fulfillProblem(route, "AUTHENTICATION_REQUIRED", {
        status: 401,
        headers: { "WWW-Authenticate": "Bearer" },
      });
      return;
    }
    await state.handle(route, request);
  });
  return {
    createdRuns: state.createdRuns,
    unhandled: state.unhandled,
    uploadName: (uploadId) => state.uploads.get(uploadId)?.name,
    seedRun: (overrides) => state.seedRun(overrides),
  };
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
    const run = { ...runBody(id, "csv-janitor"), ...overrides };
    this.runs.set(id, { run, advances: false, inputIds: [] });
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
    await fulfillJson(route, stored.run, { status: 200, headers });
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

function isReceived(upload: StoredUpload | undefined): boolean {
  return upload !== undefined && upload.content !== null;
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
  const problem: Schemas["ProblemDetails"] = {
    type: `https://goblinshift.dev/problems/${code.toLowerCase().replaceAll("_", "-")}`,
    title: code,
    status,
    detail: `API falsa do E2E: ${code}`,
    instance: new URL(route.request().url()).pathname,
    code,
    request_id: randomUUID(),
  };
  await route.fulfill({
    status,
    contentType: "application/problem+json",
    headers,
    body: JSON.stringify(problem),
  });
}
