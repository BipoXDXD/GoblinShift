import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiClient } from "../api/client";
import { ApiError, type ApiFailure } from "../api/response";
import { createUploads, type CreatedUpload } from "./api";
import {
  createLimiter,
  MAX_CONCURRENT_UPLOADS,
  retryOnRateLimit,
  type RateLimitPolicy,
} from "./queue";
import type { ContentUploader, UploadProgress } from "./xhr";

/** Falha de um arquivo: da API, ou `internal` para um defeito da própria interface. */
export type UploadFailure = ApiFailure | { kind: "internal" };

export type UploadEntryState =
  | { status: "waiting" }
  | { status: "sending"; loadedBytes: number; totalBytes: number }
  | { status: "confirming" }
  | { status: "ready"; uploadId: string; sha256: string }
  | { status: "failed"; failure: UploadFailure }
  | { status: "canceled" };

export interface UploadEntry {
  /** Identifica o arquivo na interface; o id do upload só existe depois de pronto. */
  readonly localId: string;
  readonly file: File;
  readonly state: UploadEntryState;
}

export interface UploadServices {
  readonly client: ApiClient;
  readonly uploader: ContentUploader;
  readonly rateLimit: Omit<RateLimitPolicy, "signal">;
}

interface UploadBatch {
  readonly entries: readonly UploadEntry[];
  readonly add: (files: readonly File[]) => void;
  /** Envia de novo um arquivo falho, com uma intenção nova (sem retomada). */
  readonly retry: (localId: string) => void;
  readonly cancel: (localId: string) => void;
}

const ACTIVE_STATUSES: ReadonlySet<UploadEntryState["status"]> = new Set([
  "waiting",
  "sending",
  "confirming",
]);

/**
 * Lote de uploads da Bancada: cria as intenções (POST /uploads), envia no máximo dois arquivos por
 * vez, repete o 429 RATE_LIMITED pelo Retry-After e segura a saída da página enquanto houver envio.
 */
export function useUploadBatch(services: UploadServices): UploadBatch {
  const [entries, setEntries] = useState<readonly UploadEntry[]>([]);
  const [limit] = useState(() => createLimiter(MAX_CONCURRENT_UPLOADS));
  const controllers = useRef(new Map<string, AbortController>());
  const servicesRef = useRef(services);
  useEffect(() => {
    servicesRef.current = services;
  }, [services]);

  const setState = useCallback((localId: string, state: UploadEntryState) => {
    setEntries((current) =>
      current.map((entry) => (entry.localId === localId ? { ...entry, state } : entry)),
    );
  }, []);

  const send = useCallback(
    (batch: readonly Pick<UploadEntry, "localId" | "file">[]) => {
      const signals = new Map(
        batch.map(({ localId }) => [localId, renewController(controllers.current, localId)]),
      );
      void createIntents(servicesRef.current.client, batch).then(
        (intents) => {
          batch.forEach((entry, index) => {
            const intent = intents[index];
            const signal = signals.get(entry.localId);
            if (intent === undefined || signal === undefined) {
              setState(entry.localId, { status: "failed", failure: { kind: "internal" } });
              return;
            }
            void limit(() =>
              sendOne({ ...entry, intent, signal, services: servicesRef.current, setState }),
            );
          });
        },
        (error: unknown) => {
          for (const { localId } of batch) {
            if (signals.get(localId)?.aborted !== true) {
              setState(localId, failureState(error));
            }
          }
        },
      );
    },
    [limit, setState],
  );

  const add = useCallback(
    (files: readonly File[]) => {
      const added = files.map((file) => ({ localId: crypto.randomUUID(), file }));
      setEntries((current) => [
        ...current,
        ...added.map((entry) => ({ ...entry, state: { status: "waiting" } as const })),
      ]);
      send(added);
    },
    [send],
  );

  const retry = useCallback(
    (localId: string) => {
      const entry = entries.find((candidate) => candidate.localId === localId);
      if (entry?.state.status !== "failed") {
        return;
      }
      setState(localId, { status: "waiting" });
      send([entry]);
    },
    [entries, send, setState],
  );

  const cancel = useCallback(
    (localId: string) => {
      controllers.current.get(localId)?.abort();
      setState(localId, { status: "canceled" });
    },
    [setState],
  );

  const uploading = entries.some((entry) => ACTIVE_STATUSES.has(entry.state.status));
  useEffect(() => {
    if (!uploading) {
      return undefined;
    }
    const holdPage = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", holdPage);
    return () => {
      window.removeEventListener("beforeunload", holdPage);
    };
  }, [uploading]);

  useEffect(() => {
    const active = controllers.current;
    return () => {
      for (const controller of active.values()) {
        controller.abort();
      }
    };
  }, []);

  return { entries, add, retry, cancel };
}

function renewController(controllers: Map<string, AbortController>, localId: string): AbortSignal {
  const controller = new AbortController();
  controllers.set(localId, controller);
  return controller.signal;
}

async function createIntents(
  client: ApiClient,
  batch: readonly Pick<UploadEntry, "file">[],
): Promise<readonly CreatedUpload[]> {
  return createUploads(
    client,
    batch.map(({ file }) => ({ name: file.name, size: file.size })),
  );
}

interface SendOne {
  readonly localId: string;
  readonly file: File;
  readonly intent: CreatedUpload;
  readonly signal: AbortSignal;
  readonly services: UploadServices;
  readonly setState: (localId: string, state: UploadEntryState) => void;
}

async function sendOne({ localId, file, intent, signal, services, setState }: SendOne) {
  try {
    // Cancelado enquanto esperava a vaga: o uploader recusa o sinal já abortado sem abrir conexão.
    const received = await retryOnRateLimit(
      () =>
        services.uploader.upload({
          content: intent.content,
          file,
          signal,
          onProgress: (progress) => {
            setState(localId, progressState(progress));
          },
        }),
      { ...services.rateLimit, signal },
    );
    setState(localId, { status: "ready", uploadId: received.id, sha256: received.sha256 });
  } catch (error: unknown) {
    if (!signal.aborted) {
      setState(localId, failureState(error));
    }
  }
}

function progressState(progress: UploadProgress): UploadEntryState {
  switch (progress.phase) {
    case "sending":
      return {
        status: "sending",
        loadedBytes: progress.loadedBytes,
        totalBytes: progress.totalBytes,
      };
    case "confirming":
      return { status: "confirming" };
  }
}

function failureState(error: unknown): UploadEntryState {
  const failure: UploadFailure = error instanceof ApiError ? error.failure : { kind: "internal" };
  return { status: "failed", failure };
}
