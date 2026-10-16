import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import { OPERATIONS, type PdfPressSettings } from "./schema";

const defaultsSchema = z.object({
  operation: z.enum(OPERATIONS),
  pages_per_document: z.int().positive(),
  select: z.null(),
});

const schemaBounds = z.object({
  properties: z.object({
    pages_per_document: z.object({ maximum: z.int().positive() }),
    select: z.object({
      anyOf: z.tuple([
        z.object({ maxItems: z.int().positive() }),
        z.object({ type: z.literal("null") }),
      ]),
    }),
  }),
});

/** `null` quando o catálogo não traz a versão no formato que este formulário conhece. */
export function pdfPressSettings(
  version: Pick<CatalogVersion, "defaults" | "parameters_schema">,
): PdfPressSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const bounds = schemaBounds.safeParse(version.parameters_schema);
  if (!defaults.success || !bounds.success) {
    return null;
  }
  const { pages_per_document: group, select } = bounds.data.properties;
  return {
    defaults: {
      operation: defaults.data.operation,
      pages_per_document: String(defaults.data.pages_per_document),
      cards: [],
    },
    limits: { maxPages: group.maximum, maxSelectedPages: select.anyOf[0].maxItems },
  };
}
