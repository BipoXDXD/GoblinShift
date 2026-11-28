import { Bell, BellRing } from "lucide-react";
import { useEffect, useRef, useState, type ReactElement } from "react";

import { LiveRegion } from "../components/a11y/LiveRegion";
import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import { alertsEnabled, alertsSupported, disableAlerts, requestAlerts } from "./alerts";

const T = TEXT.run.completion;

type ToggleState = "off" | "on" | "blocked";

interface CompletionAlertToggleProps {
  /** A execução terminou: o aviso não serve mais e o botão some. */
  readonly finished: boolean;
  /** Recebe o foco quando o botão some com o foco nele, que iria para o `<body>` (WCAG 2.4.3). */
  readonly onLeave: () => void;
}

/**
 * "Avisar quando terminar" (D3): botão terciário que liga a notificação do navegador. A permissão
 * só é pedida no clique; negada, o motivo aparece e o título da aba continua avisando. Sem
 * `Notification` no navegador, não aparece. O rótulo muda com o estado ("Aviso ligado"), então não
 * leva `aria-pressed`: o leitor de tela diria o estado duas vezes (APG, Button pattern).
 */
export function CompletionAlertToggle({
  finished,
  onLeave,
}: CompletionAlertToggleProps): ReactElement | null {
  const [state, setState] = useState<ToggleState>(() => (alertsEnabled() ? "on" : "off"));
  const focused = useRef(false);
  useEffect(() => {
    if (finished && focused.current) {
      focused.current = false;
      onLeave();
    }
  }, [finished, onLeave]);
  if (finished || !alertsSupported()) {
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
          onFocus={() => {
            focused.current = true;
          }}
          onBlur={() => {
            focused.current = false;
          }}
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
