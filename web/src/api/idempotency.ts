import { useCallback, useState } from "react";

interface IdempotencyKey {
  /** Chave a mandar em `Idempotency-Key`; estável entre retentativas da mesma intenção. */
  readonly key: string;
  /** Encerra as intenções (depois do sucesso): toda submissão seguinte é uma intenção nova. */
  readonly rotate: () => void;
}

/** Quantos comandos o formulário lembra; acima disso, o mais antigo ganha chave nova se voltar. */
const MAX_REMEMBERED_INTENTS = 20;

/**
 * Uma `Idempotency-Key` por intenção do usuário (2.2).
 *
 * `intent` identifica o comando (por exemplo, o corpo serializado): repetir o mesmo comando reusa a
 * chave, e o servidor devolve a resposta original; mudar o comando gera chave nova, porque a API
 * recusa a mesma chave com outro corpo (IDEMPOTENCY_KEY_REUSED). Voltar a um comando já usado
 * (A→B→A) reaproveita a chave dele, para que um A enviado cuja resposta se perdeu não vire uma Run
 * duplicada; `rotate` esquece todas.
 */
export function useIdempotencyKey(intent: string): IdempotencyKey {
  const [keys, setKeys] = useState<ReadonlyMap<string, string>>(
    () => new Map([[intent, newIdempotencyKey()]]),
  );
  let key = keys.get(intent);
  if (key === undefined) {
    // Ajuste de estado durante a renderização, o padrão do React para estado derivado de props.
    key = newIdempotencyKey();
    setKeys(remember(keys, intent, key));
  }
  const rotate = useCallback(() => {
    setKeys(new Map());
  }, []);
  return { key, rotate };
}

/** Guarda a chave da intenção nova, descartando a mais antiga acima do teto. */
function remember(
  keys: ReadonlyMap<string, string>,
  intent: string,
  key: string,
): ReadonlyMap<string, string> {
  const entries = [...keys, [intent, key] as const];
  return new Map(entries.slice(-MAX_REMEMBERED_INTENTS));
}

function newIdempotencyKey(): string {
  // UUID v4 com hífens: 36 caracteres em [0-9a-f-], dentro do formato aceito pela API.
  return crypto.randomUUID();
}
