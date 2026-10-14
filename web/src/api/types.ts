// Aliases finos sobre os tipos gerados da spec (src/api/schema.d.ts). Nenhum campo é reescrito à
// mão: a spec é a única fonte (ETS item 42).
import type { operations } from "./schema";

export type RunCommand = operations["createRun"]["requestBody"]["content"]["application/json"];
