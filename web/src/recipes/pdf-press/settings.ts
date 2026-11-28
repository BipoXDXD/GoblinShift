import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import { COMPRESSIONS, OPERATIONS, ROTATIONS, type PdfPressSettings } from "./schema";

const defaultsSchema = z.object({
  operation: z.enum(OPERATIONS),
  pages_per_document: z.int().positive(),
  select: z.null(),
  rotation: z.literal(ROTATIONS),
  compression: z.enum(COMPRESSIONS),
  strip_metadata: z.boolean(),
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

const limitsSchema = z.object({ max_output_pages: z.int().positive() });

/** `null` quando o catálogo não traz a versão no formato que este formulário conhece. */
export function pdfPressSettings(
  version: Pick<CatalogVersion, "defaults" | "parameters_schema" | "limits">,
): PdfPressSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const bounds = schemaBounds.safeParse(version.parameters_schema);
  const limits = limitsSchema.safeParse(version.limits);
  if (!defaults.success || !bounds.success || !limits.success) {
    return null;
  }
  const { pages_per_document: group, select } = bounds.data.properties;
  return {
    defaults: {
      operation: defaults.data.operation,
      pages_per_document: String(defaults.data.pages_per_document),
      rotation: defaults.data.rotation,
      compression: defaults.data.compression,
      strip_metadata: defaults.data.strip_metadata,
      cards: [],
    },
    limits: {
      maxPages: group.maximum,
      maxRanges: select.anyOf[0].maxItems,
      maxOutputPages: limits.data.max_output_pages,
    },
  };
}
