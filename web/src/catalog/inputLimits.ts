import { z } from "zod";

import type { CatalogVersion } from "./api";

/** Limites das entradas de uma execução, da versão da receita no catálogo. */
export interface InputLimits {
  readonly maxInputs: number;
  /** Soma dos tamanhos das entradas; `null` quando a versão não limita o lote. */
  readonly maxTotalBytes: number | null;
}

const limitsSchema = z.object({
  max_inputs: z.int().positive(),
  max_total_input_bytes: z.int().positive().optional(),
});

/** `null` quando o catálogo não traz os limites no formato esperado. */
export function inputLimitsOf(version: Pick<CatalogVersion, "limits">): InputLimits | null {
  const parsed = limitsSchema.safeParse(version.limits);
  if (!parsed.success) {
    return null;
  }
  return {
    maxInputs: parsed.data.max_inputs,
    maxTotalBytes: parsed.data.max_total_input_bytes ?? null,
  };
}
