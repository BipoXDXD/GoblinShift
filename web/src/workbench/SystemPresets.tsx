import { useId, type ReactElement } from "react";

import type { SystemPreset } from "../catalog/api";
import { Button } from "../components/ui/Button";
import { TEXT } from "../text";

interface SystemPresetsProps {
  readonly presets: readonly SystemPreset[];
  readonly onApply: (preset: SystemPreset) => void;
}

/**
 * Os ajustes prontos da versão (D3), numa linha de botões acima do formulário. Aplicar só preenche
 * o formulário: não grava nada e não cria preset. Sem ajustes, não ocupa espaço.
 */
export function SystemPresets({ presets, onApply }: SystemPresetsProps): ReactElement | null {
  const titleId = useId();
  const hintId = useId();
  if (presets.length === 0) {
    return null;
  }
  return (
    <div
      role="group"
      aria-labelledby={titleId}
      aria-describedby={hintId}
      className="flex flex-col gap-2"
    >
      <h3 id={titleId} className="text-base font-semibold text-fg">
        {TEXT.workbench.systemPresets.title}
      </h3>
      <p id={hintId} className="max-w-prose text-sm leading-relaxed text-fg-muted">
        {TEXT.workbench.systemPresets.hint}
      </p>
      <div className="flex flex-wrap gap-2">
        {presets.map((preset) => (
          <Button
            key={preset.key}
            type="button"
            variant="secondary"
            onClick={() => {
              onApply(preset);
            }}
          >
            {preset.name}
          </Button>
        ))}
      </div>
    </div>
  );
}
