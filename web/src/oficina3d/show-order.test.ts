import { describe, expect, it } from "vitest";

import { ShowOrder, type ShowTicket } from "./show-order";

/** Começa a troca e exige que ela precise de carga (a goblin não estava no palco). */
function begun(order: ShowOrder<string>, id: string): ShowTicket {
  const ticket = order.begin(id);
  if (ticket === null) {
    throw new Error(`${id} já estava no palco`);
  }
  return ticket;
}

/** Uma ordem com a Rúbia já no palco, como depois da primeira carga. */
function withRubiaOnStage(): ShowOrder<string> {
  const order = new ShowOrder<string>();
  const first = begun(order, "rubia");
  order.cleared();
  order.shown(first, "rubia");
  return order;
}

describe("ShowOrder", () => {
  it("com o palco vazio, a primeira troca vale", () => {
    const order = new ShowOrder<string>();

    expect(order.isCurrent(begun(order, "rubia"))).toBe(true);
  });

  it("a goblin que já está no palco não carrega de novo", () => {
    expect(withRubiaOnStage().begin("rubia")).toBeNull();
  });

  it("Nyx segurada, Fagulha escolhida: a Nyx que chega depois não vale, a Fagulha sim", () => {
    const order = withRubiaOnStage();

    const nyx = begun(order, "nyx");
    const fagulha = begun(order, "fagulha");

    expect(order.isCurrent(nyx)).toBe(false);
    expect(order.isCurrent(fagulha)).toBe(true);
  });

  it("voltar à goblin do palco antes da outra chegar invalida a outra sem carregar nada", () => {
    const order = withRubiaOnStage();
    const nyx = begun(order, "nyx");

    expect(order.begin("rubia")).toBeNull();
    expect(order.isCurrent(nyx)).toBe(false);
  });

  it("depois que o modelo antigo saiu do palco, voltar a ele carrega de novo", () => {
    const order = withRubiaOnStage();
    begun(order, "nyx");

    order.cleared();

    expect(order.begin("rubia")).not.toBeNull();
  });

  it("a troca que chega ao palco fica registrada como a mostrada", () => {
    const order = withRubiaOnStage();
    const nyx = begun(order, "nyx");
    order.cleared();

    order.shown(nyx, "nyx");

    expect(order.begin("nyx")).toBeNull();
  });

  it("uma troca vencida não se registra como mostrada", () => {
    const order = withRubiaOnStage();
    const nyx = begun(order, "nyx");
    begun(order, "fagulha");
    order.cleared();

    order.shown(nyx, "nyx");

    expect(order.begin("nyx")).not.toBeNull();
  });

  it("cancelar tudo invalida a troca em andamento", () => {
    const order = new ShowOrder<string>();
    const ticket = begun(order, "rubia");

    order.cancelAll();

    expect(order.isCurrent(ticket)).toBe(false);
  });
});
