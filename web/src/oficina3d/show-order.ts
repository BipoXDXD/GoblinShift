/** Uma troca de goblin em andamento; só vale enquanto for a mais recente. */
export type ShowTicket = number & { readonly __brand: "ShowTicket" };

/**
 * A ordem das trocas de goblin no palco (ADR 038): só a mais recente chega ao palco. Cada
 * {@link begin} vence as anteriores, inclusive quando a pessoa volta à goblin que já está lá
 * (nesse caso nada precisa carregar). Sem three: a cena consulta a ordem a cada `await`.
 */
export class ShowOrder<Id> {
  #latest = 0;
  #onStage: Id | null = null;

  /** Começa uma troca para `id`; `null` quando ela já está no palco e não há o que carregar. */
  begin(id: Id): ShowTicket | null {
    this.#latest += 1;
    return id === this.#onStage ? null : this.#ticket();
  }

  isCurrent(ticket: ShowTicket): boolean {
    return ticket === this.#latest;
  }

  /** O modelo anterior saiu do palco: nenhuma goblin está nele até {@link shown}. */
  cleared(): void {
    this.#onStage = null;
  }

  /** A troca chegou ao palco; uma troca vencida não muda nada. */
  shown(ticket: ShowTicket, id: Id): void {
    if (this.isCurrent(ticket)) {
      this.#onStage = id;
    }
  }

  /** A cena vai embora: nenhuma troca em andamento vale mais. */
  cancelAll(): void {
    this.#latest += 1;
  }

  #ticket(): ShowTicket {
    // O número só circula como ticket desta ordem; a marca impede trocá-lo por um número qualquer.
    return this.#latest as ShowTicket;
  }
}
