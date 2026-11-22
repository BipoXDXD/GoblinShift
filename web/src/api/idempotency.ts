import { useCallback, useState } from "react";

interface IdempotencyKey {
  /** Chave a mandar em `Idempotency-Key`; estável entre retentativas da mesma intenção. */
  readonly key: string;
  /** Encerra as intenções (depois do sucesso): toda submissão seguinte é uma intenção nova. */
  readonly rotate: () => void;
  /** Guarda a chave da intenção, antes do POST: voltar a ela depois reaproveita a chave. */
  readonly markSent: (intent: string) => void;
}

/** Quantos comandos enviados o formulário lembra; acima disso, o mais antigo é esquecido. */
const MAX_REMEMBERED_INTENTS = 20;

interface Keys {
  /** Chaves das intenções enviadas, na ordem em que foram enviadas pela primeira vez. */
  readonly sent: ReadonlyMap<string, string>;
  /** Chave efêmera da intenção atual, ainda não enviada: some quando o comando muda. */
  readonly draft: { readonly intent: string; readonly key: string };
}

/**
 * Uma `Idempotency-Key` por intenção do usuário (2.2).
 *
 * `intent` identifica o comando (por exemplo, o corpo serializado): repetir o mesmo comando reusa a
 * chave, e o servidor devolve a resposta original; mudar o comando gera chave nova, porque a API
 * recusa a mesma chave com outro corpo (IDEMPOTENCY_KEY_REUSED). Voltar a um comando já enviado
 * (A→B→A) reaproveita a chave dele, para que um A enviado cuja resposta se perdeu não vire uma Run
 * duplicada. Só as intenções enviadas (`markSent`) ocupam memória: digitar num campo cria uma
 * intenção por tecla, e elas não podem expulsar o comando enviado. `rotate` esquece todas.
 */
export function useIdempotencyKey(intent: string): IdempotencyKey {
  const [keys, setKeys] = useState<Keys>(() => ({
    sent: new Map(),
    draft: { intent, key: newIdempotencyKey() },
  }));
  let key = keyOf(keys, intent);
  if (key === undefined) {
    // Ajuste de estado durante a renderização, o padrão do React para estado derivado de props.
    key = newIdempotencyKey();
    setKeys({ sent: keys.sent, draft: { intent, key } });
  }
  const rotate = useCallback(() => {
    setKeys((previous) => ({
      sent: new Map(),
      draft: { intent: previous.draft.intent, key: newIdempotencyKey() },
    }));
  }, []);
  /**
   * Guarda a chave do comando que vai ser enviado. Passe a intenção do render atual: uma intenção
   * que não é a atual nem foi enviada antes não tem chave a guardar, e a chamada não faz nada.
   */
  const markSent = useCallback((sentIntent: string) => {
    setKeys((previous) => {
      const sentKey = keyOf(previous, sentIntent);
      if (sentKey === undefined || previous.sent.has(sentIntent)) {
        return previous;
      }
      return { ...previous, sent: remember(previous.sent, sentIntent, sentKey) };
    });
  }, []);
  return { key, rotate, markSent };
}

function keyOf({ sent, draft }: Keys, intent: string): string | undefined {
  return sent.get(intent) ?? (draft.intent === intent ? draft.key : undefined);
}

/** Guarda a chave da intenção enviada, descartando a mais antiga acima do teto. */
function remember(
  sent: ReadonlyMap<string, string>,
  intent: string,
  key: string,
): ReadonlyMap<string, string> {
  const entries = [...sent, [intent, key] as const];
  return new Map(entries.slice(-MAX_REMEMBERED_INTENTS));
}

function newIdempotencyKey(): string {
  // UUID v4 com hífens: 36 caracteres em [0-9a-f-], dentro do formato aceito pela API.
  return crypto.randomUUID();
}
