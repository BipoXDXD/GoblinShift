// Na CI, teste pulado reprova a execução. Um `test.skip` em runtime parece verde e não prova nada:
// o caso que não pode rodar num projeto ou modo sai dele por tag no playwright.config.ts (não é
// coletado), e qualquer skip que sobre é um teste que deixou de rodar sem ninguém ver.
import { z } from "zod";

interface ReportSuite {
  title: string;
  specs?: { title: string; tests: { projectName: string; status: string }[] }[] | undefined;
  suites?: ReportSuite[] | undefined;
}

const suiteSchema: z.ZodType<ReportSuite> = z.lazy(() =>
  z.object({
    title: z.string(),
    specs: z
      .array(
        z.object({
          title: z.string(),
          tests: z.array(z.object({ projectName: z.string(), status: z.string() })),
        }),
      )
      .optional(),
    suites: z.array(suiteSchema).optional(),
  }),
);

const reportSchema = z.object({
  suites: z.array(suiteSchema),
  stats: z.object({ skipped: z.number().int().nonnegative() }),
});

/** Os testes pulados de um relatório do reporter `json`, como `projeto › suíte › título`. */
export function skippedTests(raw: unknown): string[] {
  const report = reportSchema.parse(raw);
  const skipped: string[] = [];
  const visit = (suite: ReportSuite, parents: readonly string[]): void => {
    const path = suite.title === "" ? parents : [...parents, suite.title];
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests) {
        if (test.status === "skipped") {
          skipped.push([test.projectName, ...path, spec.title].join(" › "));
        }
      }
    }
    for (const child of suite.suites ?? []) visit(child, path);
  };
  for (const suite of report.suites) visit(suite, []);
  if (skipped.length !== report.stats.skipped) {
    throw new Error("relatório inconsistente: a contagem de pulados não bate com os testes");
  }
  return skipped;
}
