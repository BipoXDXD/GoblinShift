import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import { AUDIO_FORMATS, THUMBNAIL_COUNTS, type MediaMillSettings } from "./schema";

const defaultsSchema = z.object({
  extract_audio: z.boolean(),
  audio_format: z.enum(AUDIO_FORMATS),
  audio_stream_index: z.int().nonnegative().nullable(),
  thumbnail_count: z.union(THUMBNAIL_COUNTS.map((count) => z.literal(count))),
});

const schemaBounds = z.object({
  properties: z.object({
    audio_stream_index: z.object({
      anyOf: z.array(z.unknown()).transform((options) =>
        options.flatMap((candidate) => {
          const integer = z
            .object({ type: z.literal("integer"), maximum: z.int() })
            .safeParse(candidate);
          return integer.success ? [integer.data.maximum] : [];
        }),
      ),
    }),
  }),
});

const limitsSchema = z.object({
  thumbnail_max_side_px: z.int().positive(),
  max_audio_sample_rate_hz: z.int().positive(),
  max_audio_channels: z.int().positive(),
});

/** `null` quando o catálogo não traz a versão no formato que este formulário conhece. */
export function mediaMillSettings(
  version: Pick<CatalogVersion, "defaults" | "limits" | "parameters_schema">,
): MediaMillSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const bounds = schemaBounds.safeParse(version.parameters_schema);
  const limits = limitsSchema.safeParse(version.limits);
  const [maxStreamIndex] = bounds.success ? bounds.data.properties.audio_stream_index.anyOf : [];
  if (!defaults.success || !limits.success || maxStreamIndex === undefined) {
    return null;
  }
  const values = defaults.data;
  return {
    defaults: {
      extract_audio: values.extract_audio,
      audio_format: values.audio_format,
      audio_stream_index:
        values.audio_stream_index === null ? "" : String(values.audio_stream_index),
      thumbnail_count: values.thumbnail_count,
    },
    limits: {
      maxStreamIndex,
      thumbnailMaxSide: limits.data.thumbnail_max_side_px,
      maxSampleRateHz: limits.data.max_audio_sample_rate_hz,
      maxChannels: limits.data.max_audio_channels,
    },
  };
}
