import { z } from "zod";

const outcome = z.enum(["expected", "unexpected", "flaky", "skipped"]);
const testSchema = z.object({
  status: outcome,
  results: z.array(
    z.object({ status: z.enum(["passed", "failed", "timedOut", "skipped", "interrupted"]) }),
  ),
});

interface Suite {
  specs: { tests: z.infer<typeof testSchema>[] }[];
  suites?: Suite[] | undefined;
}

const suiteSchema: z.ZodType<Suite> = z.lazy(() =>
  z.object({
    specs: z.array(z.object({ tests: z.array(testSchema) })),
    suites: z.array(suiteSchema).optional(),
  }),
);

const count = z.number().int().nonnegative();
const reportSchema = z.object({
  suites: z.array(suiteSchema),
  stats: z.object({ expected: count, unexpected: count, flaky: count, skipped: count }),
  errors: z.array(z.unknown()),
});

type SabotageReport = z.infer<typeof reportSchema>["stats"] & {
  readonly selected: number;
  readonly executed: number;
  readonly failed: number;
  readonly errors: number;
  readonly caught: boolean;
};

export function inspectSabotageReport(raw: unknown, exitCode: number | null): SabotageReport {
  const report = reportSchema.parse(raw);
  const counts = { expected: 0, unexpected: 0, flaky: 0, skipped: 0, failed: 0 };
  let selected = 0;
  let executed = 0;

  const visit = (suite: Suite): void => {
    for (const spec of suite.specs) {
      for (const test of spec.tests) {
        selected++;
        counts[test.status]++;
        if (
          test.results.length > 0 &&
          test.results.every(({ status }) => status !== "skipped" && status !== "interrupted")
        ) {
          executed++;
        }
        if (
          test.status === "unexpected" &&
          test.results.some(({ status }) => status === "failed" || status === "timedOut")
        ) {
          counts.failed++;
        }
      }
    }
    for (const child of suite.suites ?? []) visit(child);
  };
  for (const suite of report.suites) visit(suite);

  if (outcome.options.some((status) => counts[status] !== report.stats[status])) {
    throw new Error("contagem inconsistente no relatório de defeitos plantados");
  }

  const errors = report.errors.length;
  const caught =
    exitCode === 1 &&
    counts.failed > 0 &&
    executed === selected &&
    counts.skipped === 0 &&
    counts.flaky === 0 &&
    errors === 0;
  return { ...counts, selected, executed, errors, caught };
}
