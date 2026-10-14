import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import { OUTPUT_FORMATS, type ImageForgeSettings } from "./schema";

const defaultsSchema = z.object({
  max_width: z.int(),
  max_height: z.int(),
  output_format: z.enum(OUTPUT_FORMATS),
  jpeg_quality: z.int(),
  jpeg_background: z.string(),
  make_thumbnails: z.boolean(),
});

const range = z
  .object({ minimum: z.int(), maximum: z.int() })
  .refine(({ minimum, maximum }) => minimum <= maximum)
  .transform(({ minimum, maximum }) => ({ min: minimum, max: maximum }));

const schemaBounds = z.object({
  properties: z.object({ max_width: range, max_height: range, jpeg_quality: range }),
});

const limitsSchema = z.object({ thumbnail_box_px: z.int().positive() });

/** `null` quando o catálogo não traz a versão no formato que este formulário conhece. */
export function imageForgeSettings(
  version: Pick<CatalogVersion, "defaults" | "limits" | "parameters_schema">,
): ImageForgeSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const bounds = schemaBounds.safeParse(version.parameters_schema);
  const limits = limitsSchema.safeParse(version.limits);
  if (!defaults.success || !bounds.success || !limits.success) {
    return null;
  }
  const { max_width: width, max_height: height, jpeg_quality: quality } = bounds.data.properties;
  const values = defaults.data;
  return {
    defaults: {
      max_width: String(values.max_width),
      max_height: String(values.max_height),
      output_format: values.output_format,
      jpeg_quality: String(values.jpeg_quality),
      jpeg_background: values.jpeg_background,
      make_thumbnails: values.make_thumbnails,
    },
    limits: {
      width,
      height,
      quality,
      thumbnailBox: limits.data.thumbnail_box_px,
    },
  };
}
