// Os dois modos da suíte, decididos por variável de ambiente e lidos uma vez só.
//
// - Local (padrão, o do CI): o build de dist/ servido por e2e/server/serve-dist.ts e a API falsa
//   no processo do Playwright. A chave é um canário gerado a cada execução.
// - Remoto (E2E_BASE_URL definido): a SPA e a API reais, no Compose ou na VPS. A chave vem de
//   GS_E2E_API_KEY, que só este processo lê (decisão U2 do plano de fechamento da fatia 7).
// - VPS (E2E_VPS=1, com E2E_BASE_URL): só specs/vps.spec.ts, as jornadas de leitura, no Chromium e
//   no Firefox. A chave é opcional: sem ela, as jornadas que exigem conta são puladas com o motivo.
import { randomUUID } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PRODUCTION_ORIGIN = "https://goblinshift.dev";

// Outra porta (E2E_PORT) deixa o sabotage.ts subir um servidor próprio ao lado do de sempre.
const LOCAL_PORT = Number(process.env["E2E_PORT"] ?? "4173");

interface E2EEnv {
  readonly baseUrl: string;
  readonly remote: boolean;
  /** Só as jornadas de leitura contra a VPS (specs/vps.spec.ts). */
  readonly vps: boolean;
  /** Vazia quando `hasAccount` é falso: não há chave a injetar. */
  readonly apiKey: string;
  /** Há uma conta (a chave) para as jornadas que a exigem. */
  readonly hasAccount: boolean;
  /** Onde a API falsa grava os artefatos que o servidor local entrega (modo local). */
  readonly artifactsDir: string | null;
}

function readEnv(): E2EEnv {
  const remoteUrl = process.env["E2E_BASE_URL"];
  const vps = process.env["E2E_VPS"] === "1";
  if (remoteUrl === undefined || remoteUrl === "") {
    if (vps) {
      throw new Error("E2E_VPS=1 exige E2E_BASE_URL com o endereço da VPS.");
    }
    return {
      baseUrl: `http://127.0.0.1:${String(LOCAL_PORT)}`,
      remote: false,
      vps: false,
      hasAccount: true,
      // Fixa por variável só para o sabotage.ts plantar a mesma chave no bundle.
      apiKey: process.env["GS_E2E_API_KEY"] ?? `CANARY-${randomUUID()}`,
      artifactsDir: sharedArtifactsDir(),
    };
  }
  const apiKey = process.env["GS_E2E_API_KEY"] ?? "";
  if (apiKey === "" && !vps) {
    throw new Error("E2E_BASE_URL exige GS_E2E_API_KEY com a chave da conta de teste.");
  }
  // A chave da conta de teste da VPS só vai para a produção, nunca para um host apontado à mão.
  if (vps && apiKey !== "" && new URL(remoteUrl).origin !== PRODUCTION_ORIGIN) {
    throw new Error(`Com chave, E2E_VPS=1 só roda contra ${PRODUCTION_ORIGIN}.`);
  }
  return {
    baseUrl: remoteUrl,
    remote: true,
    vps,
    apiKey,
    hasAccount: apiKey !== "",
    artifactsDir: null,
  };
}

/**
 * Pasta temporária criada pelo processo principal do Playwright, que carrega a configuração antes
 * de tudo; os workers e o servidor a herdam pela variável de ambiente.
 */
function sharedArtifactsDir(): string {
  const inherited = process.env["E2E_ARTIFACTS_DIR"];
  if (inherited !== undefined && inherited !== "") {
    return inherited;
  }
  const created = mkdtempSync(join(tmpdir(), "goblinshift-e2e-"));
  process.env["E2E_ARTIFACTS_DIR"] = created;
  return created;
}

export const E2E = readEnv();
/** Build com o aviso de e2e/fixtures/notice.json (npm run e2e:notice, ADR 031). */
export const NOTICE_BUILD = process.env["E2E_NOTICE"] === "1";
export { LOCAL_PORT };
