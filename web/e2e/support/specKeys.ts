// Uma conta e uma chave por arquivo de spec no modo remoto (issue #338): o limite de 120 leituras
// por minuto vale por conta, e a suíte inteira numa conta só estourava (133 GETs em 60 s, 429 sem
// repetição na interface, que o `expect` de 5 s via como timeout). O scripts/e2e-gvisor.sh cria as
// contas e entrega as chaves em GS_E2E_API_KEYS, um objeto JSON `{"<arquivo>.spec.ts": "<chave>"}`.
// Desktop e celular do mesmo arquivo dividem a conta (51 leituras por minuto no pior arquivo).
import { basename } from "node:path";

import { z } from "zod";

const keysSchema = z.record(z.string().min(1), z.string().min(1));

/** Identifica a conta de uma spec: o nome do arquivo, sem diretório. */
export function specKeyId(specFile: string): string {
  return basename(specFile);
}

/** Lê GS_E2E_API_KEYS; vazia ou ausente, não há chave por spec. */
export function parseSpecKeys(raw: string | undefined): ReadonlyMap<string, string> {
  if (raw === undefined || raw === "") {
    return new Map();
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Sem o texto do erro: ele pode citar um trecho do valor, que contém chaves.
    throw new Error("GS_E2E_API_KEYS não é um JSON válido.");
  }
  const keys = keysSchema.safeParse(parsed);
  if (!keys.success) {
    throw new Error('GS_E2E_API_KEYS deve ser um objeto {"<arquivo>.spec.ts": "<chave>"}.');
  }
  return new Map(Object.entries(keys.data));
}

/**
 * A chave da spec. Com chaves por spec, uma spec sem entrada é erro: cair na chave única
 * escondia de volta o consumo compartilhado que a conta por spec existe para evitar.
 */
export function pickApiKey(
  keys: ReadonlyMap<string, string>,
  fallback: string,
  id: string,
): string {
  if (keys.size === 0) {
    return fallback;
  }
  const key = keys.get(id);
  if (key === undefined) {
    throw new Error(
      `GS_E2E_API_KEYS não tem a conta de ${id}; o script cria uma por arquivo de spec.`,
    );
  }
  return key;
}
