import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import {
  AUDIO_FORMATS,
  BITRATES_KBPS,
  formatTrim,
  LOSSY_FORMATS,
  THUMBNAIL_COUNTS,
  type BitrateKbps,
  type LossyFormat,
  type MediaMillSettings,
} from "./schema";

const bitrate = z.union(BITRATES_KBPS.map((value) => z.literal(value)));

const defaultsSchema = z.object({
  extract_audio: z.boolean(),
  audio_format: z.enum(AUDIO_FORMATS),
  audio_stream_index: z.int().nonnegative().nullable(),
  thumbnail_count: z.union(THUMBNAIL_COUNTS.map((count) => z.literal(count))),
  audio_bitrate_kbps: bitrate,
  trim_start_ms: z.int().nonnegative(),
  trim_end_ms: z.int().positive().nullable(),
});

/** `maximum` do primeiro ramo inteiro de um `anyOf` (o outro é `null`). */
const integerMaximum = z.object({
  anyOf: z.array(z.unknown()).transform((options) =>
    options.flatMap((candidate) => {
      const integer = z
        .object({ type: z.literal("integer"), maximum: z.int() })
        .safeParse(candidate);
      return integer.success ? [integer.data.maximum] : [];
    }),
  ),
});

// Um ramo do `allOf`: "se audio_format é X, audio_bitrate_kbps é um destes" (ADR 063, D6).
const bitrateRule = z.object({
  if: z.object({
    properties: z.object({ audio_format: z.object({ const: z.enum(LOSSY_FORMATS) }) }),
  }),
  then: z.object({
    properties: z.object({ audio_bitrate_kbps: z.object({ enum: z.array(bitrate).nonempty() }) }),
  }),
});

const schemaBounds = z.object({
  properties: z.object({
    audio_stream_index: integerMaximum,
    trim_start_ms: z.object({ maximum: z.int().positive() }),
  }),
  allOf: z.array(z.unknown()),
});

const limitsSchema = z.object({
  thumbnail_max_side_px: z.int().positive(),
  max_audio_sample_rate_hz: z.int().positive(),
  max_audio_channels: z.int().positive(),
});

/** Os bitrates de cada formato com perda; `null` se faltar algum. */
function bitratesByFormat(
  rules: readonly unknown[],
): Readonly<Record<LossyFormat, readonly BitrateKbps[]>> | null {
  const found = new Map<LossyFormat, readonly BitrateKbps[]>();
  for (const candidate of rules) {
    const rule = bitrateRule.safeParse(candidate);
    if (rule.success) {
      found.set(
        rule.data.if.properties.audio_format.const,
        rule.data.then.properties.audio_bitrate_kbps.enum,
      );
    }
  }
  const [mp3, aac, opus] = LOSSY_FORMATS.map((format) => found.get(format));
  return mp3 === undefined || aac === undefined || opus === undefined ? null : { mp3, aac, opus };
}

/** `null` quando o catálogo não traz a versão no formato que este formulário conhece. */
export function mediaMillSettings(
  version: Pick<CatalogVersion, "defaults" | "limits" | "parameters_schema">,
): MediaMillSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const bounds = schemaBounds.safeParse(version.parameters_schema);
  const limits = limitsSchema.safeParse(version.limits);
  if (!defaults.success || !bounds.success || !limits.success) {
    return null;
  }
  const [maxStreamIndex] = bounds.data.properties.audio_stream_index.anyOf;
  const bitrates = bitratesByFormat(bounds.data.allOf);
  if (maxStreamIndex === undefined || bitrates === null) {
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
      audio_bitrate_kbps: values.audio_bitrate_kbps,
      trim_start: values.trim_start_ms === 0 ? "" : formatTrim(values.trim_start_ms),
      trim_end: values.trim_end_ms === null ? "" : formatTrim(values.trim_end_ms),
    },
    limits: {
      maxStreamIndex,
      thumbnailMaxSide: limits.data.thumbnail_max_side_px,
      maxSampleRateHz: limits.data.max_audio_sample_rate_hz,
      maxChannels: limits.data.max_audio_channels,
      maxTrimMs: bounds.data.properties.trim_start_ms.maximum,
    },
    bitratesByFormat: bitrates,
  };
}
