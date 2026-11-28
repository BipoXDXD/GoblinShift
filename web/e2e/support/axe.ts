// O que as medições do axe nas telas reais (contrast.spec.ts) e na cena 3D (oficina-3d.spec.ts)
// dividem: as regras WCAG 2.2 A/AA e a descrição legível de cada violação.

export const WCAG_22_AA = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export function describeViolation(violation: {
  id: string;
  nodes: { target: unknown; failureSummary?: string | undefined }[];
}): string {
  const nodes = violation.nodes.map(
    (node) => `${JSON.stringify(node.target)}: ${node.failureSummary ?? ""}`,
  );
  return `${violation.id}\n${nodes.join("\n")}`;
}
