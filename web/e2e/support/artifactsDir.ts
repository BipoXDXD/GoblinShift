// A pasta onde a API falsa grava os artefatos que o servidor local entrega (modo local).
//
// O processo principal do Playwright carrega a configuração antes de tudo e cria a pasta; os
// workers e o servidor a herdam pela variável de ambiente. Só quem a criou a apaga, no
// globalTeardown: sem isso, cada execução deixava uma goblinshift-e2e-* no TMPDIR (issue #420).
// Uma pasta recebida pronta (E2E_ARTIFACTS_DIR já definido) é de quem a passou.
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const DIR_VARIABLE = "E2E_ARTIFACTS_DIR";
const OWNER_VARIABLE = "E2E_ARTIFACTS_DIR_OWNER";

export function claimArtifactsDir(env: NodeJS.ProcessEnv, pid: number): string {
  const inherited = env[DIR_VARIABLE];
  if (inherited !== undefined && inherited !== "") {
    return inherited;
  }
  const created = mkdtempSync(join(tmpdir(), "goblinshift-e2e-"));
  env[DIR_VARIABLE] = created;
  env[OWNER_VARIABLE] = String(pid);
  return created;
}

export function removeOwnArtifactsDir(env: NodeJS.ProcessEnv, pid: number): void {
  const dir = env[DIR_VARIABLE];
  if (dir === undefined || dir === "" || env[OWNER_VARIABLE] !== String(pid)) {
    return;
  }
  rmSync(dir, { recursive: true, force: true });
}
