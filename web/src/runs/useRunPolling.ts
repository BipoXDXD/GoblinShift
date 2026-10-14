import { useQuery, type Query, type UseQueryResult } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { useApiClient } from "../api/ApiClientContext";
import type { ApiResponse } from "../api/response";
import { announcementFor, type RunProgressSnapshot } from "./announce";
import { getRun, type Run } from "./api";
import { nextPollDelayMs, type PollSnapshot } from "./polling";

interface RunPolling {
  /** Estado da consulta do TanStack Query; `data` é a execução já validada. */
  readonly query: UseQueryResult<Run>;
  /** Texto da região `aria-live="polite"`; muda só quando status ou fase mudam. */
  readonly announcement: string | null;
}

interface RunPollingOptions {
  /** Em [0, 1); jitter do recuo depois de falha. */
  readonly random?: () => number;
}

type RunQuery = Query<ApiResponse<Run>, Error, ApiResponse<Run>, readonly string[]>;

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

  return { query, announcement: useAnnouncement(query.data) };
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

function useAnnouncement(run: Run | undefined): string | null {
  const [tracked, setTracked] = useState<{
    snapshot: RunProgressSnapshot | null;
    announcement: string | null;
  }>({ snapshot: null, announcement: null });
  if (run !== undefined && changed(tracked.snapshot, run)) {
    const next = { status: run.status, phase: run.progress.phase };
    // Ajuste de estado durante a renderização: o padrão do React para estado derivado de dados.
    setTracked({
      snapshot: next,
      announcement: announcementFor(tracked.snapshot, next) ?? tracked.announcement,
    });
  }
  return tracked.announcement;
}

function changed(previous: RunProgressSnapshot | null, run: Run): boolean {
  return previous?.status !== run.status || previous.phase !== run.progress.phase;
}
