// Os dois modos da suíte, decididos por variável de ambiente e lidos uma vez só.
//
// - Local (padrão, o do CI): o build de dist/ servido por e2e/server/serve-dist.ts e a API falsa
//   no processo do Playwright. A chave é um canário gerado a cada execução.
// - Remoto (E2E_BASE_URL definido): a SPA e a API reais do Compose local, com escrita. A chave vem de
//   GS_E2E_API_KEY, que só este processo lê (decisão U2 do plano de fechamento da fatia 7). Com
//   GS_E2E_API_KEYS (objeto JSON), cada spec e projeto usa a sua conta: o limite de leituras por
//   minuto vale por conta (issue #338, specKeys.ts), e a chave única vira só o recurso de quem roda
//   à mão.
// - VPS (E2E_VPS=1, com E2E_BASE_URL): só specs/vps.spec.ts, as jornadas de leitura, no Chromium e
//   no Firefox. A chave é opcional: sem ela, as jornadas que exigem conta (@conta) não são coletadas.
import { randomUUID } from "node:crypto";

import { claimArtifactsDir } from "./artifactsDir.ts";
import { NOTICE_ENV, NOTICE_KINDS, type NoticeKind } from "./phases.ts";
import { parseSpecKeys } from "./specKeys.ts";
import { assertLocalWriteTarget } from "./writeTarget.ts";

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
  /** Uma chave por arquivo de spec; vazio, vale `apiKey` para todas (specKeys.ts). */
  readonly specKeys: ReadonlyMap<string, string>;
  /** Onde a API falsa grava os artefatos que o servidor local entrega (modo local). */
  readonly artifactsDir: string | null;
}

function readEnv(): E2EEnv {
  const remoteUrl = process.env["E2E_BASE_URL"];
  const productionRead = process.env["E2E_VPS"] === "1";
  const ciRead = process.env["E2E_CI_READ"] === "1";
  if (ciRead && (productionRead || remoteUrl !== "http://localhost")) {
    throw new Error("E2E_CI_READ exige somente a pilha descartável em http://localhost.");
  }
  const vps = productionRead || ciRead;
  if (remoteUrl === undefined || remoteUrl === "") {
    if (vps) {
      throw new Error("E2E_VPS=1 exige E2E_BASE_URL com o endereço da VPS.");
    }
    return {
      baseUrl: `http://127.0.0.1:${String(LOCAL_PORT)}`,
      remote: false,
      vps: false,
      hasAccount: true,
      specKeys: new Map(),
      // Fixa por variável só para o sabotage.ts plantar a mesma chave no bundle.
      apiKey: process.env["GS_E2E_API_KEY"] ?? `CANARY-${randomUUID()}`,
      artifactsDir: claimArtifactsDir(process.env, process.pid),
    };
  }
  const apiKey = process.env["GS_E2E_API_KEY"] ?? "";
  if (ciRead && apiKey === "") {
    throw new Error("E2E_CI_READ exige a conta descartável para executar todas as jornadas.");
  }
  const specKeys = vps ? new Map<string, string>() : parseSpecKeys(process.env["GS_E2E_API_KEYS"]);
  if (apiKey === "" && specKeys.size === 0 && !vps) {
    throw new Error(
      "E2E_BASE_URL exige GS_E2E_API_KEY (ou GS_E2E_API_KEYS) com a chave da conta de teste.",
    );
  }
  if (!vps) {
    assertLocalWriteTarget(remoteUrl);
  }
  // A chave da conta de teste da VPS só vai para a produção, nunca para um host apontado à mão.
  if (productionRead && apiKey !== "" && new URL(remoteUrl).origin !== PRODUCTION_ORIGIN) {
    throw new Error(`Com chave, E2E_VPS=1 só roda contra ${PRODUCTION_ORIGIN}.`);
  }
  return {
    baseUrl: remoteUrl,
    remote: true,
    vps,
    apiKey,
    hasAccount: apiKey !== "" || specKeys.size > 0,
    specKeys,
    artifactsDir: null,
  };
}

export const E2E = readEnv();

/**
 * Aviso do build servido (ADR 031), pelo valor de `E2E_NOTICE` em `NOTICE_ENV`: `1` é o de
 * e2e/fixtures/notice.json (npm run e2e:notice); `incident`, o de e2e/fixtures/notice-incident.json
 * (npm run e2e:notice-incident). Sem a variável, o build normal, sem aviso; outro valor é erro.
 */
function readNoticeKind(value: string | undefined): NoticeKind | null {
  if (value === undefined || value === "") {
    return null;
  }
  const kind = NOTICE_KINDS.find((candidate) => NOTICE_ENV[candidate] === value);
  if (kind === undefined) {
    throw new Error(`E2E_NOTICE inválido: use ${Object.values(NOTICE_ENV).join(" ou ")}`);
  }
  return kind;
}

export const NOTICE_KIND = readNoticeKind(process.env["E2E_NOTICE"]);
/** O build servido traz a faixa do incidente (fase notice-incident). */
export const INCIDENT_BUILD = NOTICE_KIND === "incident";
export { LOCAL_PORT };
