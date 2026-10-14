import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import { FileDropzone } from "./FileDropzone";

const csv = (name: string) => new File(["a,b\n1,2\n"], name, { type: "text/csv" });

describe("FileDropzone", () => {
  it("é um input de arquivo de verdade, com label", () => {
    render(<FileDropzone id="arquivos" label="Arquivos" onFiles={vi.fn()} />);

    const input = screen.getByLabelText("Arquivos");
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAttribute("multiple");
  });

  it("entrega os arquivos escolhidos pelo teclado ou clique", async () => {
    const user = userEvent.setup();
    const onFiles = vi.fn();
    render(<FileDropzone id="arquivos" label="Arquivos" onFiles={onFiles} />);

    await user.upload(screen.getByLabelText("Arquivos"), [csv("a.csv"), csv("b.csv")]);

    expect(onFiles).toHaveBeenCalledWith([
      expect.objectContaining({ name: "a.csv" }),
      expect.objectContaining({ name: "b.csv" }),
    ]);
  });

  it("limpa o input depois da escolha, para o mesmo arquivo poder voltar", async () => {
    const user = userEvent.setup();
    render(<FileDropzone id="arquivos" label="Arquivos" onFiles={vi.fn()} />);
    const input = screen.getByLabelText<HTMLInputElement>("Arquivos");

    await user.upload(input, csv("a.csv"));

    expect(input.value).toBe("");
  });

  it("aceita arquivos soltos sobre a área", () => {
    const onFiles = vi.fn();
    render(<FileDropzone id="arquivos" label="Arquivos" onFiles={onFiles} />);
    const area = screen.getByLabelText("Arquivos").parentElement?.parentElement;
    if (area === null || area === undefined) {
      throw new Error("área de soltura ausente");
    }

    fireEvent.drop(area, { dataTransfer: { files: [csv("a.csv")], types: ["Files"] } });

    expect(onFiles).toHaveBeenCalledWith([expect.objectContaining({ name: "a.csv" })]);
  });

  it("repassa os tipos aceitos ao input", () => {
    render(<FileDropzone id="arquivos" label="Arquivos" accept=".csv" onFiles={vi.fn()} />);

    expect(screen.getByLabelText("Arquivos")).toHaveAttribute("accept", ".csv");
  });

  it("liga o erro ao input", () => {
    render(
      <FileDropzone id="arquivos" label="Arquivos" error="Escolha um arquivo." onFiles={vi.fn()} />,
    );

    expect(screen.getByLabelText("Arquivos")).toHaveAccessibleDescription(
      "Erro: Escolha um arquivo.",
    );
  });

  it("não tem violações do axe", async () => {
    const { container } = render(
      <FileDropzone id="arquivos" label="Arquivos" description="CSV." onFiles={vi.fn()} />,
    );

    expect(await axeViolations(container)).toEqual([]);
  });
});
