// Regras puras da vitrine de portfólio (plano 2026-11-22-vitrine-de-portfolio, Tarefa 2).
//
// A captura (Tarefa 3) só fala com o build local e a API falsa do processo; estas funções não
// abrem navegador nem rede. Elas recusam o ambiente de outro modo (produção, VPS, chave real),
// descobrem as goblins de cada imagem pelos assets carregados (D3 a: Jade e Fagulha ficam fora) e
// montam um manifesto estável, que sai byte a byte igual entre rodadas sem mudança de interface.
import { readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// `resolve` a partir do próprio arquivo, e não `new URL(literal, import.meta.url)`: o Vite do Vitest
// troca essa forma por URL de asset, e o `fileURLToPath` recusa.
const HERE = dirname(fileURLToPath(import.meta.url));

/** Pasta só da vitrine, que o script de captura esvazia antes de gravar. */
export const VITRINE_DIR = resolve(HERE, "../../../docs/img/vitrine");

/** Quadros do GIF, fora do Git (`test-results/` já está no `web/.gitignore`). */
export const FRAMES_DIR = resolve(HERE, "../../test-results/vitrine-quadros");

/** Relógio fixo da página: cinco minutos depois do `NOW` da API falsa (2026-10-14T12:00:00Z). */
export const VITRINE_CLOCK = new Date("2026-10-14T12:05:00Z");

/**
 * Goblins fora da concessão CC BY 4.0 (ADR 030 e `docs/art/LICENSE-ART.md`, "Jade e Fagulha").
 * Decisão D3 (a): nenhuma captura do repositório pode mostrá-las.
 */
export const RESTRICTED_GOBLINS = ["jade", "fagulha"] as const;

/** Variáveis de outros modos do e2e (remoto, VPS, CI, Aviso) ou de chave real. */
const FORBIDDEN_ENV = [
  "E2E_BASE_URL",
  "E2E_VPS",
  "E2E_CI_READ",
  "E2E_NOTICE",
  "GS_E2E_API_KEY",
  "GS_E2E_API_KEYS",
] as const;

const COMMIT_SHA = /^[0-9a-f]{40}$/;
const PILHA = "build local + API falsa (web/e2e/support/fakeApi.ts)";

export interface VitrineEntry {
  readonly arquivo: string;
  readonly tela: string;
  readonly viewport: "1280x800" | "400x800";
  readonly goblins: readonly string[];
}

/** Lança antes de abrir o navegador, nomeando a primeira variável proibida definida e não vazia. */
export function assertVitrineEnv(env: Readonly<Record<string, string | undefined>>): void {
  for (const name of FORBIDDEN_ENV) {
    const value = env[name];
    if (value !== undefined && value !== "") {
      throw new Error(
        `A vitrine só roda no build local com a API falsa; remova ${name} do ambiente.`,
      );
    }
  }
}

/** Nomes das goblins: o prefixo antes do primeiro `-` de cada arquivo da pasta de arte. */
export function knownGoblins(goblinArtDir: string): string[] {
  const names = new Set<string>();
  for (const file of readdirSync(goblinArtDir)) {
    const dash = file.indexOf("-");
    if (dash > 0) {
      names.add(file.slice(0, dash));
    }
  }
  return [...names].sort();
}

function fileNameOf(url: string): string {
  const path = new URL(url, "http://vitrine.invalid/").pathname;
  return decodeURIComponent(path.slice(path.lastIndexOf("/") + 1)).toLowerCase();
}

/**
 * Goblins cujo nome aparece no nome do arquivo como `<nome>-`, no começo ou depois de um `-`
 * (`jade-256w-<hash>.avif`, `oficina-3d-jade-192w-<hash>.webp`, `rubia-escudo-48h-<hash>.avif`).
 */
export function goblinsIn(urls: readonly string[], known: readonly string[]): string[] {
  const files = urls.map(fileNameOf);
  return known
    .filter((name) =>
      files.some((file) => file.startsWith(`${name}-`) || file.includes(`-${name}-`)),
    )
    .filter((name, index, all) => all.indexOf(name) === index)
    .sort();
}

/** Manifesto sem horário e ordenado por arquivo, para sair igual entre rodadas. */
export function renderManifest(entries: readonly VitrineEntry[], commit: string): string {
  if (!COMMIT_SHA.test(commit)) {
    throw new Error(`commit precisa ter 40 dígitos hexadecimais: ${JSON.stringify(commit)}`);
  }
  const imagens = [...entries]
    .sort((a, b) => (a.arquivo < b.arquivo ? -1 : a.arquivo > b.arquivo ? 1 : 0))
    .map((e) => ({
      arquivo: e.arquivo,
      tela: e.tela,
      viewport: e.viewport,
      goblins: [...e.goblins].sort(),
    }));
  return `${JSON.stringify({ pilha: PILHA, commit, imagens }, null, 2)}\n`;
}
