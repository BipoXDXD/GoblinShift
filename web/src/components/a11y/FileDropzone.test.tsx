import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import { FileDropzone } from "./FileDropzone";

const csv = (name: string) => new File(["a,b\n1,2\n"], name, { type: "text/csv" });

describe("FileDropzone", () => {
  it("é um input de arquivo de verdade, com label", () => {
    render(<FileDropzone id="arquivos" label="Arquivos" chosenCount={0} onFiles={vi.fn()} />);

    const input = screen.getByLabelText("Arquivos");
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAttribute("multiple");
  });

  it("entrega os arquivos escolhidos pelo teclado ou clique", async () => {
    const user = userEvent.setup();
    const onFiles = vi.fn();
    render(<FileDropzone id="arquivos" label="Arquivos" chosenCount={0} onFiles={onFiles} />);

    await user.upload(screen.getByLabelText("Arquivos"), [csv("a.csv"), csv("b.csv")]);

    expect(onFiles).toHaveBeenCalledWith([
      expect.objectContaining({ name: "a.csv" }),
      expect.objectContaining({ name: "b.csv" }),
    ]);
  });

  it("limpa o input depois da escolha, para o mesmo arquivo poder voltar", async () => {
    const user = userEvent.setup();
    render(<FileDropzone id="arquivos" label="Arquivos" chosenCount={0} onFiles={vi.fn()} />);
    const input = screen.getByLabelText<HTMLInputElement>("Arquivos");

    await user.upload(input, csv("a.csv"));

    expect(input.value).toBe("");
  });

  it("aceita arquivos soltos sobre a área", () => {
    const onFiles = vi.fn();
    const { container } = render(
      <FileDropzone id="arquivos" label="Arquivos" chosenCount={0} onFiles={onFiles} />,
    );
    const area = container.querySelector("[data-dragging]");
    if (area === null) {
      throw new Error("área de soltura ausente");
    }

    fireEvent.drop(area, { dataTransfer: { files: [csv("a.csv")], types: ["Files"] } });

    expect(onFiles).toHaveBeenCalledWith([expect.objectContaining({ name: "a.csv" })]);
  });

  it("repassa os tipos aceitos ao input", () => {
    render(
      <FileDropzone
        id="arquivos"
        label="Arquivos"
        chosenCount={0}
        accept=".csv"
        onFiles={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Arquivos")).toHaveAttribute("accept", ".csv");
  });

  it("liga o erro ao input", () => {
    render(
      <FileDropzone
        id="arquivos"
        label="Arquivos"
        chosenCount={0}
        error="Escolha um arquivo."
        onFiles={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Arquivos")).toHaveAccessibleDescription(
      "Erro: Escolha um arquivo. Nenhum arquivo escolhido",
    );
  });

  describe("controle no idioma da interface, não no do navegador", () => {
    it("o botão visível é o label; o input real fica visualmente oculto e na ordem do Tab", async () => {
      const user = userEvent.setup();
      render(<FileDropzone id="arquivos" label="Arquivos" chosenCount={0} onFiles={vi.fn()} />);
      const input = screen.getByLabelText("Arquivos");

      expect(input.closest("label")).toHaveTextContent("Arquivos");
      expect(input).toHaveClass("sr-only");
      await user.tab();
      expect(input).toHaveFocus();
    });

    it("clicar no label abre a escolha e entrega os arquivos", async () => {
      const user = userEvent.setup();
      const onFiles = vi.fn();
      render(<FileDropzone id="arquivos" label="Arquivos" chosenCount={0} onFiles={onFiles} />);
      const label = screen.getByLabelText("Arquivos").closest("label");
      if (label === null) {
        throw new Error("label ausente");
      }

      await user.upload(label, csv("a.csv"));

      expect(onFiles).toHaveBeenCalledWith([expect.objectContaining({ name: "a.csv" })]);
    });

    it.each([
      [0, "Nenhum arquivo escolhido"],
      [1, "1 arquivo escolhido"],
      [2, "2 arquivos escolhidos"],
    ])("com %i arquivo(s), diz em pt-BR quantos foram escolhidos", (count, status) => {
      render(<FileDropzone id="arquivos" label="Arquivos" chosenCount={count} onFiles={vi.fn()} />);

      expect(screen.getByText(status)).toBeVisible();
      expect(screen.getByLabelText("Arquivos")).toHaveAccessibleDescription(status);
    });

    it("nenhum texto vem do navegador nem em inglês", () => {
      const { container } = render(
        <FileDropzone id="arquivos" label="Arquivos" chosenCount={0} onFiles={vi.fn()} />,
      );

      expect(container).toHaveTextContent(/^ArquivosNenhum arquivo escolhido$/);
      expect(container.textContent).not.toMatch(/choose|no file|browse|selected/i);
    });
  });

  it("não tem violações do axe", async () => {
    const { container } = render(
      <FileDropzone
        id="arquivos"
        label="Arquivos"
        chosenCount={0}
        description="CSV."
        onFiles={vi.fn()}
      />,
    );

    expect(await axeViolations(container)).toEqual([]);
  });
});
