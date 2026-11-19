import { z } from "zod";

const outcome = z.enum(["expected", "unexpected", "flaky", "skipped"]);
const resultStatus = z.enum(["passed", "failed", "timedOut", "skipped", "interrupted"]);
const testSchema = z.object({
  status: outcome,
  expectedStatus: resultStatus,
  projectName: z.string(),
  results: z.array(
    z.object({
      status: resultStatus,
      error: z.object({ message: z.string().optional(), stack: z.string().optional() }).optional(),
      errors: z.array(z.unknown()),
    }),
  ),
});
const specSchema = z.object({ file: z.string(), tests: z.array(testSchema) });

interface Suite {
  specs: z.infer<typeof specSchema>[];
  suites?: Suite[] | undefined;
}

const suiteSchema: z.ZodType<Suite> = z.lazy(() =>
  z.object({
    specs: z.array(specSchema),
    suites: z.array(suiteSchema).optional(),
  }),
);

const count = z.number().int().nonnegative();
const reportSchema = z.object({
  suites: z.array(suiteSchema),
  stats: z.object({ expected: count, unexpected: count, flaky: count, skipped: count }),
  errors: z.array(z.unknown()),
});

interface ExpectedFailure {
  readonly spec: string;
  readonly project: string;
  readonly assertions: readonly string[];
}

type SabotageReport = z.infer<typeof reportSchema>["stats"] & {
  readonly selected: number;
  readonly executed: number;
  readonly failed: number;
  readonly attributed: number;
  readonly unattributed: readonly { marker: string | null; location: string | null }[];
  readonly errors: number;
  readonly caught: boolean;
};

export function inspectSabotageReport(
  raw: unknown,
  exitCode: number | null,
  expected: ExpectedFailure,
): SabotageReport {
  const report = reportSchema.parse(raw);
  const markers = new Set(expected.assertions.map((id) => `Error: [sabotage:${id}]`));
  const counts = { expected: 0, unexpected: 0, flaky: 0, skipped: 0, failed: 0, attributed: 0 };
  let selected = 0;
  let executed = 0;
  const selection = { mismatch: false };
  const unattributed: SabotageReport["unattributed"][number][] = [];

  const visit = (suite: Suite): void => {
    for (const spec of suite.specs) {
      for (const test of spec.tests) {
        selected++;
        counts[test.status]++;
        const matchesScenario =
          spec.file.split("/").at(-1) === expected.spec && test.projectName === expected.project;
        selection.mismatch ||= !matchesScenario || test.expectedStatus !== "passed";

        if (
          test.results.length > 0 &&
          test.results.every(({ status }) => status === "passed" || status === "failed")
        ) {
          executed++;
        }
        if (
          test.status !== "unexpected" ||
          !test.results.some(({ status }) => status === "failed" || status === "timedOut")
        ) {
          continue;
        }
        counts.failed++;
        if (
          matchesScenario &&
          test.expectedStatus === "passed" &&
          test.results.every(({ status, error, errors }) => {
            const [marker = "", separator] = error?.message?.split(/\r?\n/, 2) ?? [];
            return (
              status === "failed" && errors.length === 1 && separator === "" && markers.has(marker)
            );
          })
        ) {
          counts.attributed++;
        } else {
          const failure = test.results.find(
            ({ status }) => status === "failed" || status === "timedOut",
          );
          const firstLine = failure?.error?.message?.split(/\r?\n/, 1)[0] ?? "";
          unattributed.push({
            marker: /^Error: \[sabotage:([a-z0-9-]+)\]$/.exec(firstLine)?.[1] ?? null,
            location:
              /\/([a-z0-9-]+\.spec\.ts:\d+:\d+)\b/.exec(failure?.error?.stack ?? "")?.[1] ?? null,
          });
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
    counts.attributed > 0 &&
    counts.attributed === counts.unexpected &&
    executed === selected &&
    !selection.mismatch &&
    counts.skipped === 0 &&
    counts.flaky === 0 &&
    errors === 0;
  return { ...counts, selected, executed, unattributed, errors, caught };
}
