import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import { moveItem, ReorderableList } from "./ReorderableList";

interface Item {
  readonly id: string;
  readonly name: string;
}

const INITIAL: readonly Item[] = [
  { id: "1", name: "a.csv" },
  { id: "2", name: "b.csv" },
  { id: "3", name: "c.csv" },
];

function Harness(): ReactElement {
  const [items, setItems] = useState(INITIAL);
  return (
    <ReorderableList
      label="Ordem das entradas"
      items={items}
      getKey={(item) => item.id}
      getName={(item) => item.name}
      renderItem={(item) => <span>{item.name}</span>}
      onMove={(move) => {
        setItems((current) => moveItem(current, move));
      }}
    />
  );
}

function order(): string[] {
  const list = screen.getByRole("list", { name: "Ordem das entradas" });
  return within(list)
    .getAllByRole("listitem")
    .map((item) => item.querySelector("span")?.textContent ?? "");
}

function button(name: string): HTMLElement {
  return screen.getByRole("button", { name });
}

describe("moveItem", () => {
  it.each([
    [0, 2, ["b", "c", "a"]],
    [2, 0, ["c", "a", "b"]],
    [1, 1, ["a", "b", "c"]],
  ])("move de %i para %i", (from, to, expected) => {
    expect(moveItem(["a", "b", "c"], { from, to })).toEqual(expected);
  });

  it.each([
    [-1, 0],
    [0, 3],
    [3, 0],
  ])("devolve a lista igual para posição fora dela (%i → %i)", (from, to) => {
    expect(moveItem(["a", "b", "c"], { from, to })).toEqual(["a", "b", "c"]);
  });

  it("não altera a lista recebida", () => {
    const list = ["a", "b"];

    moveItem(list, { from: 0, to: 1 });

    expect(list).toEqual(["a", "b"]);
  });
});

describe("ReorderableList", () => {
  it("descreve como reordenar sem arrastar", () => {
    render(<Harness />);

    expect(screen.getByRole("list", { name: "Ordem das entradas" })).toHaveAccessibleDescription(
      /Mover para cima e Mover para baixo/,
    );
  });

  it("os botões dizem qual item movem", () => {
    render(<Harness />);

    expect(button("Mover para cima b.csv")).toBeInTheDocument();
    expect(button("Mover para baixo b.csv")).toBeInTheDocument();
  });

  it("move para baixo pelo botão", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(button("Mover para baixo a.csv"));

    expect(order()).toEqual(["b.csv", "a.csv", "c.csv"]);
  });

  it("move para cima pelo teclado (Enter no botão)", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    button("Mover para cima c.csv").focus();

    await user.keyboard("{Enter}");

    expect(order()).toEqual(["a.csv", "c.csv", "b.csv"]);
  });

  it("mantém o foco no mesmo botão do item movido", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    button("Mover para baixo a.csv").focus();

    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}");

    expect(order()).toEqual(["b.csv", "c.csv", "a.csv"]);
    expect(button("Mover para baixo a.csv")).toHaveFocus();
  });

  it("devolve o foco ao botão movido, que o navegador perde ao mover o nó", () => {
    // O jsdom não tira o foco de um nó movido, como o Chrome faz; o teste confere a devolução.
    render(<Harness />);
    const moved = button("Mover para baixo a.csv");
    const focus = vi.spyOn(moved, "focus");

    fireEvent.click(moved);

    expect(focus).toHaveBeenCalled();
  });

  it("atalho Alt + seta move o item que tem o foco", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    button("Mover para cima b.csv").focus();

    await user.keyboard("{Alt>}{ArrowDown}{/Alt}");

    expect(order()).toEqual(["a.csv", "c.csv", "b.csv"]);
    expect(button("Mover para cima b.csv")).toHaveFocus();
  });

  it("anuncia a nova posição", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(button("Mover para baixo a.csv"));

    expect(screen.getByRole("status")).toHaveTextContent("a.csv movido para a posição 2 de 3.");
  });

  it("nas pontas, o botão fica indisponível, mas continua focável", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const first = button("Mover para cima a.csv");

    await user.click(first);

    expect(first).toHaveAttribute("aria-disabled", "true");
    expect(order()).toEqual(["a.csv", "b.csv", "c.csv"]);
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("Tab percorre os botões na ordem da lista", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.tab();
    await user.tab();
    await user.tab();

    expect(button("Mover para cima b.csv")).toHaveFocus();
  });

  it("aceita arrasto de um item sobre outro", () => {
    render(<Harness />);
    const [first, , third] = screen.getAllByRole("listitem");
    if (first === undefined || third === undefined) {
      throw new Error("lista sem itens");
    }
    const dataTransfer = { setData: () => undefined, effectAllowed: "", dropEffect: "" };

    fireEvent.dragStart(first, { dataTransfer });
    fireEvent.dragOver(third, { dataTransfer });
    fireEvent.drop(third, { dataTransfer });

    expect(order()).toEqual(["b.csv", "c.csv", "a.csv"]);
  });

  it("não tem violações do axe", async () => {
    const { container } = render(<Harness />);

    expect(await axeViolations(container)).toEqual([]);
  });
});
