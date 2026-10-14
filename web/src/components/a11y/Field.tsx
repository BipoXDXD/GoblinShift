import { CircleAlert } from "lucide-react";
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
  /** `inline` põe o controle antes do label, na mesma linha: o jeito de checkbox e radio. */
  readonly layout?: "stacked" | "inline";
  readonly children: (control: FieldControlProps) => ReactElement;
}

/**
 * Campo com label, descrição e erro ligados ao controle por `htmlFor`, `aria-describedby` e
 * `aria-invalid`. O erro começa com "Erro:" em texto e tem ícone, para não depender de cor.
 * A descrição e o erro vêm antes do controle, como no GOV.UK: quem lê chega ao campo já sabendo.
 */
export function Field({
  id,
  label,
  description,
  error,
  layout = "stacked",
  children,
}: FieldProps): ReactElement {
  const descriptionId = description === undefined ? null : `${id}-descricao`;
  const errorId = hasText(error) ? `${id}-erro` : null;
  const control = children(controlProps({ id, descriptionId, errorId }));
  const labelElement = (
    <label htmlFor={id} className="text-sm font-semibold text-fg">
      {label}
    </label>
  );
  const help = (
    <>
      {descriptionId !== null && (
        <p id={descriptionId} className="max-w-prose text-sm leading-relaxed text-fg-muted">
          {description}
        </p>
      )}
      {errorId !== null && <FieldError id={errorId} message={error ?? ""} />}
    </>
  );
  if (layout === "inline") {
    return (
      <div className="flex flex-col gap-1">
        <div className="flex min-h-11 items-center gap-3">
          {control}
          {labelElement}
        </div>
        <div className="flex flex-col gap-1 pl-8">{help}</div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {labelElement}
      {help}
      {control}
    </div>
  );
}

/** Erro de um campo ou grupo: ícone, "Erro:" e a mensagem, na cor de perigo. */
export function FieldError({ id, message }: { id: string; message: string }): ReactElement {
  return (
    <p id={id} className="flex items-start gap-2 text-sm font-semibold text-danger-fg">
      <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      {TEXT.field.errorPrefix}
      {message}
    </p>
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
