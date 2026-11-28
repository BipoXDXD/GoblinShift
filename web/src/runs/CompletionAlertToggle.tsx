import { Bell, BellRing } from "lucide-react";
import { useState, type ReactElement } from "react";

import { LiveRegion } from "../components/a11y/LiveRegion";
import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import { alertsEnabled, alertsSupported, disableAlerts, requestAlerts } from "./alerts";

const T = TEXT.run.completion;

type ToggleState = "off" | "on" | "blocked";

/**
 * "Avisar quando terminar" (D3): botão terciário que liga a notificação do navegador. A permissão
 * só é pedida no clique; negada, o motivo aparece e o título da aba continua avisando. Sem
 * `Notification` no navegador, não aparece.
 */
export function CompletionAlertToggle(): ReactElement | null {
  const [state, setState] = useState<ToggleState>(() => (alertsEnabled() ? "on" : "off"));
  if (!alertsSupported()) {
    return null;
  }
  const on = state === "on";
  const toggle = async (): Promise<void> => {
    if (on) {
      disableAlerts();
      setState("off");
      return;
    }
    setState(await requestAlerts());
  };
  return (
    <div className="flex flex-col gap-2">
      <div>
        <Button
          type="button"
          variant="tertiary"
          aria-pressed={on}
          onClick={() => {
            void toggle();
          }}
        >
          {on ? (
            <BellRing aria-hidden="true" className="size-5" />
          ) : (
            <Bell aria-hidden="true" className="size-5" />
          )}
          {on ? T.toggleOn : T.toggleOff}
        </Button>
      </div>
      <LiveRegion message={state === "blocked" ? T.blocked : null} />
    </div>
  );
}
