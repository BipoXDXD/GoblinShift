// Conferência pelo loader real do Playwright, sem iniciar navegador ou servidor.
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { z } from "zod";

interface ListedTest {
  projectName: string;
  timeout: number;
  expectedStatus: string;
}

interface ListedSpec {
  id: string;
  tests: ListedTest[];
}

interface ListedSuite {
  title: string;
  specs?: ListedSpec[] | undefined;
  suites?: ListedSuite[] | undefined;
}

const suiteSchema: z.ZodType<ListedSuite> = z.lazy(() =>
  z.object({
    title: z.string(),
    specs: z
      .array(
        z.object({
          id: z.string(),
          tests: z.array(
            z.object({
              projectName: z.string(),
              timeout: z.number(),
              expectedStatus: z.string(),
            }),
          ),
        }),
      )
      .optional(),
    suites: z.array(suiteSchema).optional(),
  }),
);
const reportSchema = z.object({
  errors: z.array(z.unknown()),
  config: z.object({
    workers: z.number(),
    projects: z.array(z.object({ outputDir: z.string() })),
  }),
  suites: z.array(suiteSchema),
});

type Phase = "ui" | "gpu" | "notice";
interface SelectedTest {
  project: string;
  gpu: boolean;
  timeout: number;
  expectedStatus: string;
}
interface Selection {
  tests: Map<string, SelectedTest>;
  workers: number;
  outputDirs: Set<string>;
}

const require = createRequire(import.meta.url);
const cli = require.resolve("@playwright/test/cli");
const configDir = dirname(fileURLToPath(import.meta.url));
const temporary = mkdtempSync(join(tmpdir(), "gob-e2e-partition-"));

function fail(reason: string): never {
  throw new Error(reason);
}

function list(phase: Phase | undefined, notice = false): Selection {
  // Só entradas locais explícitas: sem URL remota, conta ou segredo herdado do ambiente.
  const env = {
    PATH: `${dirname(process.execPath)}:/usr/bin:/bin`,
    CI: "1",
    E2E_ARTIFACTS_DIR: temporary,
    ...(phase === undefined ? {} : { E2E_PHASE: phase }),
    ...(notice ? { E2E_NOTICE: "1" } : {}),
  };
  const result = spawnSync(
    process.execPath,
    [cli, "test", "-c", configDir, "--list", "--reporter=json"],
    {
      cwd: resolve(configDir, ".."),
      env,
      encoding: "utf8",
      timeout: 15_000,
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  if (result.status !== 0 || result.error !== undefined) {
    fail("Falha ao listar a seleção local do Playwright");
  }
  let report: z.output<typeof reportSchema>;
  try {
    const raw: unknown = JSON.parse(result.stdout);
    report = reportSchema.parse(raw);
  } catch {
    fail("Relatório de seleção inválido");
  }
  if (report.errors.length !== 0) fail("Seleção com erros de carregamento");
  const tests = new Map<string, SelectedTest>();
  function visit(suite: ListedSuite, parents: readonly string[]): void {
    const ancestry = [...parents, suite.title];
    for (const spec of suite.specs ?? []) {
      for (const selected of spec.tests) {
        const key = `${selected.projectName}:${spec.id}`;
        if (tests.has(key)) fail("ID de teste duplicado");
        tests.set(key, {
          project: selected.projectName,
          gpu: ancestry.includes("com GPU"),
          timeout: selected.timeout,
          expectedStatus: selected.expectedStatus,
        });
      }
    }
    for (const child of suite.suites ?? []) visit(child, ancestry);
  }
  for (const suite of report.suites) visit(suite, []);
  return {
    tests,
    workers: report.config.workers,
    outputDirs: new Set(report.config.projects.map((project) => project.outputDir)),
  };
}

function equalTests(expected: Selection, actual: Selection): void {
  if (actual.tests.size !== expected.tests.size) fail("A partição perdeu ou adicionou testes");
  for (const [id, entry] of expected.tests) {
    const found = actual.tests.get(id);
    if (found === undefined || JSON.stringify(found) !== JSON.stringify(entry)) {
      fail("Identidade, timeout ou estado esperado mudou na partição");
    }
  }
}

try {
  const canonical = list(undefined);
  const ui = list("ui");
  const gpu = list("gpu");
  const noticeCanonical = list(undefined, true);
  const notice = list("notice", true);
  if ([canonical, ui, gpu, notice].some((selection) => selection.tests.size === 0)) {
    fail("Partição vazia não é evidência de cobertura");
  }
  if (gpu.workers !== 1) fail("A fase GPU precisa de exatamente um worker");
  const combined = new Map(ui.tests);
  for (const [id, entry] of gpu.tests) {
    if (combined.has(id)) fail("Teste repetido entre as fases");
    combined.set(id, entry);
  }
  equalTests(canonical, { ...ui, tests: combined });
  equalTests(noticeCanonical, notice);
  for (const [id, entry] of canonical.tests) {
    if (!(entry.gpu ? gpu : ui).tests.has(id)) fail("Teste foi para a fase incorreta");
  }
  const usedOutputs = new Set<string>();
  for (const selection of [ui, gpu, notice]) {
    for (const directory of selection.outputDirs) {
      if (usedOutputs.has(directory)) fail("As fases sobrescrevem os mesmos resultados");
      usedOutputs.add(directory);
    }
  }
  const byProject: Record<string, number> = {};
  for (const entry of gpu.tests.values()) {
    byProject[entry.project] = (byProject[entry.project] ?? 0) + 1;
  }
  console.log(
    JSON.stringify({
      exact_partition: true,
      canonical: canonical.tests.size,
      ui: ui.tests.size,
      gpu: gpu.tests.size,
      notice: notice.tests.size,
      gpu_workers: gpu.workers,
      gpu_by_project: byProject,
    }),
  );
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
