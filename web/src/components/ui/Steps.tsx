import { Circle, CircleCheck, CircleDot, type LucideIcon } from "lucide-react";
import type { ReactElement } from "react";

import type { RunPhase } from "../../runs/api";
import { TEXT } from "../../text";

const T = TEXT.run;

/** Fases em ordem; `retry_wait` não é etapa, é uma espera entre tentativas. */
const ORDERED_PHASES = ["inspecting", "processing", "packaging", "finalizing"] as const;

type StepState = "done" | "current" | "pending";

const STEP_LOOK: Record<StepState, { readonly icon: LucideIcon; readonly text: string }> = {
  done: { icon: CircleCheck, text: "text-fg-muted" },
  current: { icon: CircleDot, text: "font-semibold text-fg" },
  pending: { icon: Circle, text: "text-fg-muted" },
};

/**
 * Etapas da execução (NN/g: etapas quando o tempo não é previsível). A atual tem
 * `aria-current="step"`; as anteriores dizem "concluída" em texto, e o ícone muda de forma.
 */
export function Steps({ phase }: { readonly phase: RunPhase }): ReactElement {
  const currentIndex = ORDERED_PHASES.findIndex((step) => step === phase);
  return (
    <ol aria-label={T.stepsLabel} className="flex flex-col gap-2 md:flex-row md:gap-6">
      {ORDERED_PHASES.map((step, index) => {
        const state = stepState(index, currentIndex);
        const { icon: Icon, text } = STEP_LOOK[state];
        return (
          <li
            key={step}
            className={`flex items-center gap-2 text-sm ${text}`}
            {...(state === "current" ? { "aria-current": "step" } : {})}
          >
            <Icon aria-hidden="true" className="size-4" />
            {T.steps[step]}
            {state === "done" && <span className="sr-only"> {T.stepDone}</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** Com `retry_wait` (índice -1) nenhuma etapa é a atual e nenhuma está concluída. */
function stepState(index: number, currentIndex: number): StepState {
  if (currentIndex === -1 || index > currentIndex) {
    return "pending";
  }
  return index === currentIndex ? "current" : "done";
}
