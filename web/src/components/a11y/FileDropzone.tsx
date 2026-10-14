import { useState, type ChangeEvent, type DragEvent, type ReactElement } from "react";

import { Field } from "./Field";

interface FileDropzoneProps {
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  readonly error?: string | null;
  /** Valor do atributo `accept` do input, por exemplo `.csv,text/csv`. */
  readonly accept?: string;
  readonly onFiles: (files: readonly File[]) => void;
}

/**
 * Escolha de arquivos por um `<input type="file">` real, com label: funciona por teclado, leitor de
 * tela e clique. Soltar arquivos sobre a área é só um atalho a mais. `data-dragging` marca o
 * arrasto em curso para o tema (D9) estilizar.
 */
export function FileDropzone({
  id,
  label,
  description,
  error,
  accept,
  onFiles,
}: FileDropzoneProps): ReactElement {
  const [dragging, setDragging] = useState(false);

  const onChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []);
    // Sem limpar, escolher de novo o mesmo arquivo não dispara `change`.
    input.value = "";
    if (files.length > 0) {
      onFiles(files);
    }
  };

  const onDragOver = (event: DragEvent<HTMLDivElement>): void => {
    if (carriesFiles(event)) {
      event.preventDefault();
      setDragging(true);
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) {
      onFiles(files);
    }
  };

  return (
    // Soltar arquivos é só um atalho do mouse; o controle acessível é o input de dentro, então a
    // área não precisa de papel nem de teclado próprios.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      data-dragging={dragging}
      onDragOver={onDragOver}
      onDragLeave={() => {
        setDragging(false);
      }}
      onDrop={onDrop}
    >
      <Field
        id={id}
        label={label}
        {...(description === undefined ? {} : { description })}
        {...(error === undefined ? {} : { error })}
      >
        {(control) => (
          <input
            {...control}
            type="file"
            multiple
            {...(accept === undefined ? {} : { accept })}
            onChange={onChange}
          />
        )}
      </Field>
    </div>
  );
}

function carriesFiles(event: DragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}
