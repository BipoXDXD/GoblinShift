// Reprova a execução do Playwright com teste pulado (support/skipGuard.ts). Recebe os relatórios
// do reporter `json` que o config grava na CI.
import { readFileSync } from "node:fs";

import { skippedTests } from "./support/skipGuard.ts";

const reports = process.argv.slice(2);
if (reports.length === 0) {
  console.error("uso: node e2e/check-no-skips.ts <relatório.json>...");
  process.exit(2);
}

let total = 0;
for (const path of reports) {
  const skipped = skippedTests(JSON.parse(readFileSync(path, "utf8")));
  for (const title of skipped) console.error(`pulado: ${title}`);
  total += skipped.length;
}
if (total > 0) {
  console.error(
    `${String(total)} teste(s) pulado(s): tire o caso do projeto por tag no playwright.config.ts`,
  );
  process.exit(1);
}
console.log(`nenhum teste pulado em ${String(reports.length)} relatório(s)`);
