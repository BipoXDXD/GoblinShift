import type { ChannelFactory } from "../account/sessionSignal";

/**
 * BroadcastChannel em memória: as instâncias de mesmo nome da mesma rede se ouvem, como abas da
 * mesma origem. Quem posta não recebe a própria mensagem, a entrega é assíncrona e o canal fechado
 * recusa o envio com `InvalidStateError`, como no navegador.
 */
class FakeBroadcastChannel extends EventTarget implements BroadcastChannel {
  onmessage: BroadcastChannel["onmessage"] = null;
  onmessageerror: BroadcastChannel["onmessageerror"] = null;
  closed = false;

  readonly name: string;
  readonly #peers: Set<FakeBroadcastChannel>;

  constructor(name: string, peers: Set<FakeBroadcastChannel>) {
    super();
    this.name = name;
    this.#peers = peers;
    peers.add(this);
  }

  postMessage(message: unknown): void {
    if (this.closed) {
      throw new DOMException("BroadcastChannel fechado", "InvalidStateError");
    }
    for (const peer of this.#peers) {
      if (peer !== this && peer.name === this.name) {
        queueMicrotask(() => {
          if (!peer.closed) {
            peer.dispatchEvent(new MessageEvent("message", { data: message }));
          }
        });
      }
    }
  }

  close(): void {
    this.closed = true;
    this.#peers.delete(this);
  }
}

/** Uma rede nova de canais falsos; cada chamada da fábrica é uma aba. */
export function fakeChannelNetwork(): ChannelFactory & {
  readonly opened: readonly FakeBroadcastChannel[];
} {
  const peers = new Set<FakeBroadcastChannel>();
  const opened: FakeBroadcastChannel[] = [];
  const create = (name: string): FakeBroadcastChannel => {
    const channel = new FakeBroadcastChannel(name, peers);
    opened.push(channel);
    return channel;
  };
  return Object.assign(create, { opened });
}

/** As mensagens que chegam a `channel`, na ordem. */
export function receivedBy(channel: BroadcastChannel): unknown[] {
  const messages: unknown[] = [];
  channel.addEventListener("message", (event: MessageEvent<unknown>) => {
    messages.push(event.data);
  });
  return messages;
}
