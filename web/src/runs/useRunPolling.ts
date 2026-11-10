import { useQuery, type Query, type UseQueryResult } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";

import { useApiClient } from "../api/ApiClientContext";
import type { ApiResponse } from "../api/response";
import { announcementFor, type RunProgressSnapshot } from "./announce";
import { getRun, type RunDetail, type RunStatus } from "./api";
import { isPersistentFailure, nextPollDelayMs, type PollSnapshot } from "./polling";

interface RunPolling {
  /** Estado da consulta do TanStack Query; `data` é a execução já validada. */
  readonly query: UseQueryResult<RunDetail>;
  /** Texto da região `aria-live="polite"`; muda só quando status ou fase mudam. */
  readonly announcement: string | null;
  /**
   * Avisa que outra região da página já anunciou este status (a resposta do cancelamento); a
   * próxima mudança para ele não é repetida. Corrida aceita: se o polling ler o status novo antes
   * de a resposta do pedido chegar, as duas regiões anunciam. Fechá-la exigiria levar a resposta
   * do POST ao cache do polling, e o CancelRun decidiu não fazer atualização otimista.
   */
  readonly acknowledge: (status: RunStatus) => void;
  /**
   * Falha a mostrar em alerta, ou `null`. Sem execução na tela, qualquer falha. Com ela, só a
   * persistente (polling.ts): a oscilação da rede some na próxima consulta e não vira anúncio.
   */
  readonly failure: Error | null;
}

interface RunPollingOptions {
  /** Em [0, 1); jitter do recuo depois de falha. */
  readonly random?: () => number;
}

type RunQuery = Query<ApiResponse<RunDetail>, Error, ApiResponse<RunDetail>, readonly string[]>;

/**
 * Acompanha uma execução por polling (2.2): a próxima consulta espera o Retry-After da resposta,
 * para no estado terminal, recua em falha transitória e fica parada com a aba oculta. Sem
 * `retry` do TanStack: a própria próxima consulta é a nova tentativa, com o recuo de polling.ts.
 */
export function useRunPolling(runId: string, options: RunPollingOptions = {}): RunPolling {
  const client = useApiClient();
  const random = options.random ?? Math.random;
  const consecutiveFailures = useRef(0);

  const delayAfter = (query: RunQuery): number | false => {
    const snapshot = pollSnapshot(query, consecutiveFailures.current);
    return snapshot === null ? false : nextPollDelayMs(snapshot, random);
  };

  const query = useQuery({
    queryKey: ["runs", runId] as const,
    queryFn: async ({ signal }) => {
      try {
        const response = await getRun(client, runId, signal);
        consecutiveFailures.current = 0;
        return response;
      } catch (error: unknown) {
        consecutiveFailures.current += 1;
        throw error;
      }
    },
    select: (response) => response.data,
    retry: false,
    refetchInterval: delayAfter,
    refetchIntervalInBackground: false,
    // Ao voltar para a aba, só consulta se a espera pedida já passou.
    refetchOnWindowFocus: (focused) => {
      const delayMs = delayAfter(focused);
      return delayMs !== false && Date.now() - lastUpdatedAt(focused) >= delayMs;
    },
  });

  const failureStreak = useFailureStreak(query);
  const failure =
    query.error !== null &&
    (query.data === undefined || isPersistentFailure(query.error, failureStreak))
      ? query.error
      : null;
  return { query, failure, ...useAnnouncement(query.data) };
}

/**
 * Falhas seguidas, para a renderização; o `consecutiveFailures` do polling é ref, lido só fora
 * dela. Cada falha nova aumenta `errorUpdateCount`; o sucesso zera o `error` e a contagem.
 */
function useFailureStreak(query: UseQueryResult<RunDetail>): number {
  const [tracked, setTracked] = useState({ errors: 0, streak: 0 });
  const errors = query.errorUpdateCount;
  const recovered = query.error === null;
  if (errors !== tracked.errors || (recovered && tracked.streak !== 0)) {
    // Ajuste de estado durante a renderização, como em useAnnouncement.
    const streak = recovered ? 0 : tracked.streak + errors - tracked.errors;
    setTracked({ errors, streak });
    return streak;
  }
  return tracked.streak;
}

function pollSnapshot(query: RunQuery, consecutiveFailures: number): PollSnapshot | null {
  const { state } = query;
  if (state.status === "error") {
    return { kind: "failure", error: state.error, consecutiveFailures };
  }
  if (state.data === undefined) {
    return null;
  }
  return {
    kind: "success",
    status: state.data.data.status,
    retryAfterMs: state.data.retryAfterMs,
  };
}

function lastUpdatedAt(query: RunQuery): number {
  return Math.max(query.state.dataUpdatedAt, query.state.errorUpdatedAt);
}

function useAnnouncement(run: RunDetail | undefined): {
  readonly announcement: string | null;
  readonly acknowledge: (status: RunStatus) => void;
} {
  const [tracked, setTracked] = useState<{
    snapshot: RunProgressSnapshot | null;
    announcement: string | null;
    acknowledged: RunStatus | null;
  }>({ snapshot: null, announcement: null, acknowledged: null });
  if (run !== undefined && changed(tracked.snapshot, run)) {
    const next = { status: run.status, phase: run.progress.phase };
    // Ajuste de estado durante a renderização: o padrão do React para estado derivado de dados.
    setTracked({
      snapshot: next,
      announcement:
        announcementFor(tracked.snapshot, next, tracked.acknowledged) ?? tracked.announcement,
      // Vale só para uma transição: o status já foi silenciado uma vez e não se repete na
      // Execução, então a marca não serve para mais nada.
      acknowledged: next.status === tracked.acknowledged ? null : tracked.acknowledged,
    });
  }
  const acknowledge = useCallback((status: RunStatus) => {
    setTracked((current) => ({ ...current, acknowledged: status }));
  }, []);
  return { announcement: tracked.announcement, acknowledge };
}

function changed(previous: RunProgressSnapshot | null, run: RunDetail): boolean {
  return previous?.status !== run.status || previous.phase !== run.progress.phase;
}
