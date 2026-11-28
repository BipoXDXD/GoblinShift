// As fases da suíte local na CI e o aviso que o build de cada uma traz: a fonte única que o
// playwright.config.ts, o env.ts e o check-phase-partition.ts leem. Importar não tem efeito
// colateral; o env.ts, ao contrário, lê o ambiente e cria a pasta de artefatos.

export const NOTICE_KINDS = ["terms-change", "incident"] as const;
export type NoticeKind = (typeof NOTICE_KINDS)[number];
/** Valor de `E2E_NOTICE` que seleciona o build de cada aviso (ADR 031). */
export const NOTICE_ENV = {
  "terms-change": "1",
  incident: "incident",
} as const satisfies Readonly<Record<NoticeKind, string>>;

export const PHASES = ["ui", "gpu", "notice", "notice-incident"] as const;
export type Phase = (typeof PHASES)[number];

/** Aviso do build que cada fase mede: nenhum, o de mudança nos termos ou o incidente. */
export const PHASE_NOTICE: Readonly<Record<Phase, NoticeKind | null>> = {
  ui: null,
  gpu: null,
  notice: "terms-change",
  "notice-incident": "incident",
};

export function isPhase(value: string): value is Phase {
  return PHASES.some((phase) => phase === value);
}
