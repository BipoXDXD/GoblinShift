import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import { FIT_MODES, OUTPUT_FORMATS, type ImageForgeSettings } from "./schema";

const defaultsSchema = z.object({
  max_width: z.int(),
  max_height: z.int(),
  output_format: z.enum(OUTPUT_FORMATS),
  jpeg_quality: z.int(),
  jpeg_background: z.string(),
  make_thumbnails: z.boolean(),
  fit: z.enum(FIT_MODES),
  scale_percent: z.int(),
  webp_quality: z.int(),
  webp_lossless: z.boolean(),
});

/** Faixa e default de um inteiro no JSON Schema da versão. */
const range = z
  .object({ minimum: z.int(), maximum: z.int(), default: z.int() })
  .refine(({ minimum, maximum }) => minimum <= maximum);

const schemaBounds = z.object({
  properties: z.object({
    max_width: range,
    max_height: range,
    jpeg_quality: range,
    jpeg_background: z.object({ default: z.string() }),
    scale_percent: range,
    webp_quality: range,
    webp_lossless: z.object({ default: z.boolean() }),
  }),
});

const limitsSchema = z.object({ thumbnail_box_px: z.int().positive() });

function bounds({ minimum, maximum }: z.infer<typeof range>): { min: number; max: number } {
  return { min: minimum, max: maximum };
}

/**
 * `null` quando o catálogo não traz a versão no formato que este formulário conhece (a v1, sem
 * `fit`, por exemplo). Os defaults do schema são o que um controle desabilitado envia: os
 * `defaults` da versão podem chegar trocados pelos de um preset (WorkbenchPage).
 */
export function imageForgeSettings(
  version: Pick<CatalogVersion, "defaults" | "limits" | "parameters_schema">,
): ImageForgeSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const schema = schemaBounds.safeParse(version.parameters_schema);
  const limits = limitsSchema.safeParse(version.limits);
  if (!defaults.success || !schema.success || !limits.success) {
    return null;
  }
  const properties = schema.data.properties;
  const values = defaults.data;
  return {
    defaults: {
      max_width: String(values.max_width),
      max_height: String(values.max_height),
      output_format: values.output_format,
      jpeg_quality: String(values.jpeg_quality),
      jpeg_background: values.jpeg_background,
      make_thumbnails: values.make_thumbnails,
      fit: values.fit,
      scale_percent: String(values.scale_percent),
      webp_quality: String(values.webp_quality),
      webp_lossless: values.webp_lossless,
    },
    disabledValues: {
      max_width: String(properties.max_width.default),
      max_height: String(properties.max_height.default),
      jpeg_quality: String(properties.jpeg_quality.default),
      jpeg_background: properties.jpeg_background.default,
      scale_percent: String(properties.scale_percent.default),
      webp_quality: String(properties.webp_quality.default),
      webp_lossless: properties.webp_lossless.default,
    },
    limits: {
      width: bounds(properties.max_width),
      height: bounds(properties.max_height),
      quality: bounds(properties.jpeg_quality),
      scale: bounds(properties.scale_percent),
      webpQuality: bounds(properties.webp_quality),
      thumbnailBox: limits.data.thumbnail_box_px,
    },
  };
}
