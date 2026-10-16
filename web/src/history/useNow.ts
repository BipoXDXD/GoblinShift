import { useEffect, useState } from "react";

/**
 * O relógio relido a cada `intervalMs`, para o que depende da hora (resultados que vencem) não
 * ficar parado com a tela aberta por horas.
 */
export function useNow(now: () => number, intervalMs: number): number {
  const [nowMs, setNowMs] = useState(now);
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
