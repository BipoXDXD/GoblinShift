import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import { fakeDir, fakeFile, fakeItem } from "../../test/fakeEntries";
import type { FolderPick } from "../../workbench/folderFiles";
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

    fireEvent.drop(area, {
      dataTransfer: { files: [csv("a.csv")], items: [], types: ["Files"] },
    });

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

  describe("Escolher pasta", () => {
    /** O jsdom não tem `webkitdirectory`; o navegador de verdade, sim (menos o iOS). */
    function withFolderSupport(): void {
      Object.defineProperty(HTMLInputElement.prototype, "webkitdirectory", {
        configurable: true,
        value: false,
        writable: true,
      });
    }

    beforeEach(withFolderSupport);
    afterEach(() => {
      Reflect.deleteProperty(HTMLInputElement.prototype, "webkitdirectory");
    });

    function renderWithFolder(onPick = vi.fn<(pick: FolderPick) => void>(), onFiles = vi.fn()) {
      const view = render(
        <FileDropzone
          id="arquivos"
          label="Arquivos"
          chosenCount={0}
          description="CSV."
          onFiles={onFiles}
          folder={{ maxFiles: 5, onPick }}
        />,
      );
      return { ...view, onPick, onFiles };
    }

    function dropArea(container: HTMLElement): Element {
      const area = container.querySelector("[data-dragging]");
      if (area === null) {
        throw new Error("área de soltura ausente");
      }
      return area;
    }

    it("sem suporte a pasta, o botão não aparece", () => {
      Reflect.deleteProperty(HTMLInputElement.prototype, "webkitdirectory");
      renderWithFolder();

      expect(screen.queryByLabelText("Escolher pasta")).not.toBeInTheDocument();
    });

    it("é um input de pasta de verdade, focável depois do de arquivos e com a mesma descrição", async () => {
      const user = userEvent.setup();
      renderWithFolder();
      const folder = screen.getByLabelText<HTMLInputElement>("Escolher pasta");

      expect(folder.webkitdirectory).toBe(true);
      expect(folder).toHaveAttribute("multiple");
      expect(folder).toHaveClass("sr-only");
      expect(folder.closest("label")).toHaveClass("file-picker");
      expect(folder).toHaveAccessibleDescription("CSV. Nenhum arquivo escolhido");
      await user.tab();
      await user.tab();
      expect(folder).toHaveFocus();
    });

    it("entrega a pasta escolhida já filtrada e pelo teto", async () => {
      const user = userEvent.setup();
      const { onPick } = renderWithFolder();
      const files = ["a.csv", ".DS_Store"].map((name) => {
        const file = csv(name);
        Object.defineProperty(file, "webkitRelativePath", { value: `tabelas/${name}` });
        return file;
      });

      await user.upload(screen.getByLabelText("Escolher pasta"), files);

      const pick = onPick.mock.calls[0]?.[0];
      expect(pick?.files.map((file) => file.name)).toEqual(["a.csv"]);
      expect(pick).toMatchObject({ skippedHidden: 1, skippedUnreadable: 0, overflow: false });
    });

    it("soltar uma pasta não envia o item da pasta", async () => {
      const { container, onPick, onFiles } = renderWithFolder();
      const dir = fakeDir("fotos", [[fakeFile("a.png"), fakeFile("b.png")]]);

      fireEvent.drop(dropArea(container), {
        dataTransfer: {
          files: [new File([], "fotos")],
          items: [fakeItem(dir)],
          types: ["Files"],
        },
      });

      await vi.waitFor(() => {
        expect(onPick).toHaveBeenCalledOnce();
      });
      const pick = onPick.mock.calls[0]?.[0];
      expect(pick?.files.map((file) => file.name)).toEqual(["a.png", "b.png"]);
      expect(pick?.overflow).toBe(false);
      expect(onFiles).not.toHaveBeenCalled();
    });

    it("soltar uma pasta acima do teto entrega o estouro, sem arquivos", async () => {
      const { container, onPick } = renderWithFolder();
      const many = Array.from({ length: 6 }, (_, i) => fakeFile(`${String(i)}.csv`));

      fireEvent.drop(dropArea(container), {
        dataTransfer: { files: [], items: [fakeItem(fakeDir("grande", [many]))], types: ["Files"] },
      });

      await vi.waitFor(() => {
        expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ files: [], overflow: true }));
      });
    });

    it("pasta que termina de ser lida depois de o poço sair não entrega nada", async () => {
      const { container, onPick, unmount } = renderWithFolder();

      fireEvent.drop(dropArea(container), {
        dataTransfer: {
          files: [],
          items: [fakeItem(fakeDir("fotos", [[fakeFile("a.png")]]))],
          types: ["Files"],
        },
      });
      unmount();
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(onPick).not.toHaveBeenCalled();
    });

    it("não tem violações do axe com os dois botões", async () => {
      const { container } = renderWithFolder();

      expect(await axeViolations(container)).toEqual([]);
    });
  });
});
