import { useCallback, useState } from "react";

interface IdempotencyKey {
  /** Chave a mandar em `Idempotency-Key`; estável entre retentativas da mesma intenção. */
  readonly key: string;
  /** Encerra a intenção (depois do sucesso): a próxima submissão é uma intenção nova. */
  readonly rotate: () => void;
}

/**
 * Uma `Idempotency-Key` por intenção do usuário (2.2).
 *
 * `intent` identifica o comando (por exemplo, o corpo serializado): repetir o mesmo comando reusa a
 * chave, e o servidor devolve a resposta original; mudar o comando gera chave nova, porque a API
 * recusa a mesma chave com outro corpo (IDEMPOTENCY_KEY_REUSED).
 */
export function useIdempotencyKey(intent: string): IdempotencyKey {
  const [current, setCurrent] = useState(() => ({ intent, key: newIdempotencyKey() }));
  if (current.intent !== intent) {
    // Ajuste de estado durante a renderização, o padrão do React para estado derivado de props.
    setCurrent({ intent, key: newIdempotencyKey() });
  }
  const rotate = useCallback(() => {
    setCurrent((previous) => ({ intent: previous.intent, key: newIdempotencyKey() }));
  }, []);
  return { key: current.key, rotate };
}

function newIdempotencyKey(): string {
  // UUID v4 com hífens: 36 caracteres em [0-9a-f-], dentro do formato aceito pela API.
  return crypto.randomUUID();
}
