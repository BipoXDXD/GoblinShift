/**
 * Relógio do servidor, estimado pelo header `Date` das respostas da API (D9).
 *
 * Datas que o servidor manda (validade dos resultados, das chaves, início do período) são
 * comparadas com este relógio, e não com o do aparelho, que pode estar horas fora. A resolução é
 * de 1 s, e o erro, de até uma ida e volta. Propriedades, não métodos: o App passa `clock.now` e
 * `clock.observe` soltos, sem `this`.
 */
export interface ServerClock {
  /** O relógio local mais a última diferença observada; sem observação, o local. */
  readonly now: () => number;
  /** Registra o `Date` de uma resposta; ausente ou inválido mantém a última diferença válida. */
  readonly observe: (dateHeader: string | null) => void;
}

export function createServerClock(localNow: () => number): ServerClock {
  let offsetMs = 0;
  return {
    now: () => localNow() + offsetMs,
    observe: (dateHeader) => {
      if (dateHeader === null) {
        return;
      }
      const value = dateHeader.trim();
      const serverMs = Date.parse(value);
      // Só HTTP-date (RFC 9110, 5.6.7), o mesmo critério do Retry-After (retryAfter.ts).
      if (Number.isNaN(serverMs) || !value.endsWith("GMT")) {
        return;
      }
      offsetMs = serverMs - localNow();
    },
  };
}
