import { useEffect, useState } from "react";

/**
 * O relógio relido a cada `intervalMs`, para o que depende da hora (resultados que vencem) não
 * ficar parado com a tela aberta por horas. Relido também quando `resyncKey` muda: o relógio do
 * servidor (serverClock.ts) só se acerta com uma resposta da API, que chega depois da montagem.
 */
export function useNow(now: () => number, intervalMs: number, resyncKey?: unknown): number {
  const [nowMs, setNowMs] = useState(now);
  // Ajuste durante o render, não num efeito: a tela já pinta a lista nova com o relógio acertado.
  const [seenKey, setSeenKey] = useState(resyncKey);
  if (!Object.is(seenKey, resyncKey)) {
    setSeenKey(resyncKey);
    setNowMs(now());
  }
  useEffect(() => {
    const timer = setInterval(() => {
      setNowMs(now());
    }, intervalMs);
    return () => {
      clearInterval(timer);
    };
  }, [now, intervalMs]);
  return nowMs;
}
