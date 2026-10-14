import type { ReactElement, ReactNode } from "react";

import { TEXT } from "../../text";

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
 * do grupo inteiro ficam ligados ao `fieldset` por `aria-describedby`.
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
    <fieldset {...(describedBy === "" ? {} : { "aria-describedby": describedBy })}>
      <legend>{legend}</legend>
      {descriptionId !== null && <p id={descriptionId}>{description}</p>}
      {errorId !== null && (
        <p id={errorId}>
          {TEXT.field.errorPrefix}
          {error}
        </p>
      )}
      {children}
    </fieldset>
  );
}
