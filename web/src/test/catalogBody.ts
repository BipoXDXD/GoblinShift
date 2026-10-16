// Corpo de GET /api/v1/catalog como a API o devolve hoje (só os trechos do schema que a interface
// lê). Cada teste do Vitest parte daqui e troca o que precisa com `withVersion` (catalog.ts). Fica
// num módulo sem Vitest porque a API falsa do E2E (e2e/support/fakeApi.ts) serve o mesmo corpo.
export const CATALOG_BODY = {
  recipes: [
    {
      recipe_id: "csv-janitor",
      versions: [
        {
          version: 1,
          status: "current",
          limits: {
            max_inputs: 5,
            max_input_bytes: 33554432,
            table_bytes: 8388608,
            records: 20000,
            columns: 100,
            record_bytes: 1048576,
            field_bytes: 65536,
          },
          defaults: {
            case_rules: [],
            deduplicate_by: [],
            filters: [],
            formula_guard: "none",
            input_delimiter: ",",
            output_columns: null,
            output_delimiter: ",",
            trim_columns: [],
          },
          parameters_schema: {
            type: "object",
            properties: {
              filters: { type: "array", maxItems: 5 },
            },
          },
        },
      ],
    },
    {
      recipe_id: "image-forge",
      versions: [
        {
          version: 1,
          status: "current",
          limits: {
            max_inputs: 100,
            max_input_bytes: 33554432,
            max_pixels: 24000000,
            max_axis_px: 8192,
            thumbnail_box_px: 320,
          },
          defaults: {
            jpeg_background: "#FFFFFF",
            jpeg_quality: 85,
            make_thumbnails: false,
            max_height: 1600,
            max_width: 1600,
            output_format: "preserve",
          },
          parameters_schema: {
            type: "object",
            properties: {
              max_width: { type: "integer", minimum: 16, maximum: 4096, default: 1600 },
              max_height: { type: "integer", minimum: 16, maximum: 4096, default: 1600 },
              jpeg_quality: { type: "integer", minimum: 60, maximum: 95, default: 85 },
            },
          },
        },
      ],
    },
    {
      recipe_id: "downloads-detox",
      versions: [
        {
          version: 1,
          status: "current",
          limits: { max_inputs: 100, max_input_bytes: 33554432, max_total_input_bytes: 503316480 },
          defaults: {
            deduplicate: true,
            group_by: "kind",
            name_mode: "preserve",
            name_prefix: "file",
          },
          parameters_schema: {
            type: "object",
            properties: {
              name_prefix: { type: "string", minLength: 1, maxLength: 40, default: "file" },
            },
          },
        },
      ],
    },
    {
      recipe_id: "media-mill",
      versions: [
        {
          version: 1,
          status: "current",
          limits: {
            max_inputs: 5,
            max_input_bytes: 268435456,
            max_total_input_bytes: 536870912,
            max_duration_s: 300,
            max_long_side_px: 1920,
            max_short_side_px: 1080,
            max_fps: 60,
            max_audio_sample_rate_hz: 48000,
            max_audio_channels: 2,
            thumbnail_max_side_px: 640,
          },
          defaults: {
            audio_format: "wav",
            audio_stream_index: null,
            extract_audio: true,
            thumbnail_count: 1,
          },
          parameters_schema: {
            type: "object",
            properties: {
              audio_stream_index: {
                anyOf: [{ type: "integer", minimum: 0, maximum: 15 }, { type: "null" }],
                default: null,
              },
              thumbnail_count: { type: "integer", enum: [0, 1, 3], default: 1 },
            },
          },
        },
      ],
    },
  ],
} as const;
