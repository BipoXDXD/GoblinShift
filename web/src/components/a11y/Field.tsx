import type { ReactElement } from "react";

import { TEXT } from "../../text";

/** Atributos que o controle (input, select, textarea) precisa receber para ficar ligado ao campo. */
export interface FieldControlProps {
  readonly id: string;
  readonly "aria-describedby"?: string;
  readonly "aria-invalid"?: true;
}

interface FieldProps {
  /** Id do controle; também é o alvo dos links do resumo de erros. */
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  /** Erro já em texto para a pessoa; `null` ou ausente quando o valor está certo. */
  readonly error?: string | null;
  readonly children: (control: FieldControlProps) => ReactElement;
}

/**
 * Campo com label, descrição e erro ligados ao controle por `htmlFor`, `aria-describedby` e
 * `aria-invalid`. O erro começa com "Erro:" em texto, para não depender de cor.
 */
export function Field({ id, label, description, error, children }: FieldProps): ReactElement {
  const descriptionId = description === undefined ? null : `${id}-descricao`;
  const errorId = hasText(error) ? `${id}-erro` : null;
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      {children(controlProps({ id, descriptionId, errorId }))}
      {descriptionId !== null && <p id={descriptionId}>{description}</p>}
      {errorId !== null && (
        <p id={errorId}>
          {TEXT.field.errorPrefix}
          {error}
        </p>
      )}
    </div>
  );
}

interface DescribedControl {
  readonly id: string;
  readonly descriptionId: string | null;
  readonly errorId: string | null;
}

function controlProps({ id, descriptionId, errorId }: DescribedControl): FieldControlProps {
  const describedBy = [descriptionId, errorId].filter((value) => value !== null);
  return {
    id,
    ...(describedBy.length > 0 ? { "aria-describedby": describedBy.join(" ") } : {}),
    ...(errorId === null ? {} : { "aria-invalid": true }),
  };
}

function hasText(value: string | null | undefined): value is string {
  return value !== undefined && value !== null && value !== "";
}
