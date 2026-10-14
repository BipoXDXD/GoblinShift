import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import { GROUP_BY_OPTIONS, NAME_MODES, type DownloadsDetoxSettings } from "./schema";

const defaultsSchema = z.object({
  deduplicate: z.boolean(),
  group_by: z.enum(GROUP_BY_OPTIONS),
  name_mode: z.enum(NAME_MODES),
  name_prefix: z.string(),
});

const schemaBounds = z.object({
  properties: z.object({ name_prefix: z.object({ maxLength: z.int().positive() }) }),
});

/** `null` quando o catálogo não traz a versão no formato que este formulário conhece. */
export function downloadsDetoxSettings(
  version: Pick<CatalogVersion, "defaults" | "parameters_schema">,
): DownloadsDetoxSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const bounds = schemaBounds.safeParse(version.parameters_schema);
  if (!defaults.success || !bounds.success) {
    return null;
  }
  return {
    defaults: defaults.data,
    limits: { maxPrefixChars: bounds.data.properties.name_prefix.maxLength },
  };
}
