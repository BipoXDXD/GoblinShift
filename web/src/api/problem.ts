import { z } from "zod";

// Espelho em runtime do ProblemDetails da spec (src/api/schema.d.ts). Os testes de tipo em
// problem.test.ts falham se a spec mudar e este schema não acompanhar.
export const PROBLEM_CODES = [
  "BAD_REQUEST",
  "JSON_INVALID",
  "IDEMPOTENCY_KEY_INVALID",
  "AUTHENTICATION_REQUIRED",
  "REAUTH_REQUIRED",
  "INSUFFICIENT_SCOPE",
  "FORBIDDEN",
  "CSRF_FAILED",
  "NOT_FOUND",
  "METHOD_NOT_ALLOWED",
  "REQUEST_TIMEOUT",
  "STATE_CONFLICT",
  "IDEMPOTENCY_IN_PROGRESS",
  "ACTIVE_RUN_LIMIT",
  "GONE",
  "BODY_TOO_LARGE",
  "UNSUPPORTED_MEDIA_TYPE",
  "VALIDATION_FAILED",
  "IDEMPOTENCY_KEY_REUSED",
  "INPUT_NOT_AVAILABLE",
  "RECIPE_VERSION_RETIRED",
  "QUOTA_EXCEEDED",
  "RATE_LIMITED",
  "INTERNAL_ERROR",
  "QUEUE_FULL",
  "SERVICE_UNAVAILABLE",
] as const;

const fieldErrorSchema = z.object({
  field: z.string(),
  code: z.string(),
  message: z.string(),
});

const problemSchema = z.object({
  type: z.string(),
  title: z.string(),
  status: z.number().int(),
  detail: z.string(),
  instance: z.string(),
  code: z.enum(PROBLEM_CODES),
  request_id: z.string(),
  errors: z.array(fieldErrorSchema).nullable().exactOptional(),
});

/** problem+json (RFC 9457) com `code` do catálogo da API. */
export type ApiProblem = z.infer<typeof problemSchema>;

/** O problema já validado, ou `null` se o corpo não for um problem+json do catálogo. */
export function parseProblem(raw: unknown): ApiProblem | null {
  const result = problemSchema.safeParse(raw);
  return result.success ? result.data : null;
}
