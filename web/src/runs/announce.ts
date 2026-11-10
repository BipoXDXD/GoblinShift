import { TEXT } from "../text";
import type { RunPhase, RunStatus } from "./api";

const STATUS_MESSAGES: Record<RunStatus, string> = TEXT.run.announce.statuses;
const PHASE_MESSAGES: Record<RunPhase, string> = TEXT.run.announce.phases;

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
