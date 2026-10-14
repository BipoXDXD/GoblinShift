import type { RunPhase, RunStatus } from "./api";

// Textos neutros até a decisão de voz e idioma (D11, D12); ficam juntos para trocar num lugar só.
const STATUS_MESSAGES: Record<RunStatus, string> = {
  queued: "Execução na fila.",
  running: "Execução em andamento.",
  stopping: "Cancelando a execução.",
  succeeded: "Execução concluída.",
  succeeded_with_warnings: "Execução concluída com avisos.",
  canceled: "Execução cancelada.",
  failed: "A execução falhou.",
};

const PHASE_MESSAGES: Record<RunPhase, string> = {
  inspecting: "Inspecionando as entradas.",
  processing: "Processando os arquivos.",
  packaging: "Empacotando os resultados.",
  finalizing: "Finalizando.",
  retry_wait: "Aguardando uma nova tentativa.",
};

export interface RunProgressSnapshot {
  readonly status: RunStatus;
  readonly phase: RunPhase | null;
}

/**
 * Texto para a região `aria-live="polite"`, só quando status ou fase mudam (2.2); `null` quando
 * não há o que anunciar, inclusive na primeira leitura.
 */
export function announcementFor(
  previous: RunProgressSnapshot | null,
  next: RunProgressSnapshot,
): string | null {
  if (previous === null) {
    return null;
  }
  if (previous.status !== next.status) {
    return STATUS_MESSAGES[next.status];
  }
  if (next.phase !== null && previous.phase !== next.phase) {
    return PHASE_MESSAGES[next.phase];
  }
  return null;
}
