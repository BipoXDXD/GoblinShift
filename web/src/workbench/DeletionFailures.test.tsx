import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { FailedDeletion } from "../upload/useUploadBatch";
import { DeletionFailures } from "./DeletionFailures";

// Sem espaço: só cabe a 320 px se quebrar dentro da palavra (WCAG 1.4.10). O jsdom não faz
// layout, então o teste confere a classe.
const LONG_NAME =
  "relatorio_clientes_consolidado_2026_novembro_final_revisado_pela_contabilidade_v3.csv";

function failed(uploadId: string, name: string): FailedDeletion {
  return { uploadId, name, failure: { kind: "internal" } };
}

describe("DeletionFailures: nome de arquivo longo", () => {
  it("com várias falhas, cada nome da lista quebra linha em qualquer ponto", () => {
    render(
      <DeletionFailures
        failures={[failed("u1", LONG_NAME), failed("u2", "curto.csv")]}
        onRetry={() => undefined}
      />,
    );

    for (const item of screen.getAllByRole("listitem")) {
      expect(item).toHaveClass("wrap-anywhere");
    }
  });

  it("com uma falha, o título que traz o nome quebra linha em qualquer ponto", () => {
    render(<DeletionFailures failures={[failed("u1", LONG_NAME)]} onRetry={() => undefined} />);

    expect(screen.getByText(new RegExp(LONG_NAME.replaceAll(".", "\\.")))).toHaveClass(
      "wrap-anywhere",
    );
  });
});
