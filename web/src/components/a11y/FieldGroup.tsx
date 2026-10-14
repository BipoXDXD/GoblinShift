import type { ReactElement, ReactNode } from "react";

import { FieldError } from "./Field";

interface FieldGroupProps {
  /** Prefixo dos ids da descrição e do erro do grupo. */
  readonly id: string;
  readonly legend: string;
  readonly description?: string;
  readonly error?: string | null;
  readonly children: ReactNode;
}

/**
 * Grupo de controles (radios, linhas de uma lista) com `fieldset` e `legend`; a descrição e o erro
 * do grupo inteiro ficam ligados ao `fieldset` por `aria-describedby`. Mais espaço entre grupos
 * (24 px) do que entre o label e o controle (8 px), para não haver espaço ambíguo (RUI cap. 3).
 */
export function FieldGroup({
  id,
  legend,
  description,
  error,
  children,
}: FieldGroupProps): ReactElement {
  const descriptionId = description === undefined ? null : `${id}-descricao`;
  const errorId = error === undefined || error === null || error === "" ? null : `${id}-erro`;
  const describedBy = [descriptionId, errorId].filter((value) => value !== null).join(" ");
  return (
    <fieldset
      className="flex flex-col gap-6"
      {...(describedBy === "" ? {} : { "aria-describedby": describedBy })}
    >
      <legend className="mb-2 text-base font-semibold text-fg">{legend}</legend>
      {descriptionId !== null && (
        <p id={descriptionId} className="-mt-4 max-w-prose text-sm leading-relaxed text-fg-muted">
          {description}
        </p>
      )}
      {errorId !== null && <FieldError id={errorId} message={error ?? ""} />}
      {children}
    </fieldset>
  );
}
