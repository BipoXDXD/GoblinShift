import { Upload } from "lucide-react";
import { useState, type ChangeEvent, type DragEvent, type ReactElement } from "react";

import { TEXT } from "../../text";
import { buttonClassName } from "../ui/Button";
import { FieldError } from "./Field";

interface FileDropzoneProps {
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  readonly error?: string | null;
  /** Valor do atributo `accept` do input, por exemplo `.csv,text/csv`. */
  readonly accept?: string;
  /** Quantos arquivos já estão na Bancada; o texto ao lado do botão diz isso em pt-BR. */
  readonly chosenCount: number;
  readonly onFiles: (files: readonly File[]) => void;
}

/**
 * Escolha de arquivos por um `<input type="file">` real: funciona por teclado, leitor de tela e
 * clique. O botão e o "nenhum arquivo" nativos vêm no idioma do navegador, então o input fica
 * visualmente oculto (mas focável) dentro de um label com cara de botão secundário, e um texto
 * próprio diz quantos arquivos foram escolhidos. Soltar arquivos sobre a área é só um atalho a
 * mais. A área é um poço embutido que sobe de elevação enquanto há arquivos sendo arrastados por
 * cima (`data-dragging`, RUI cap. 6).
 */
export function FileDropzone({
  id,
  label,
  description,
  error,
  accept,
  chosenCount,
  onFiles,
}: FileDropzoneProps): ReactElement {
  const [dragging, setDragging] = useState(false);
  const descriptionId = description === undefined ? null : `${id}-descricao`;
  const errorId = error === undefined || error === null || error === "" ? null : `${id}-erro`;
  const statusId = `${id}-estado`;
  const describedBy = [descriptionId, errorId, statusId].filter((value) => value !== null);

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
      className="flex flex-col gap-3 rounded-lg border-2 border-dashed border-line bg-surface-sunken p-4 shadow-sunken transition data-[dragging=true]:border-solid data-[dragging=true]:bg-dropzone-active data-[dragging=true]:shadow-float md:p-6"
      onDragOver={onDragOver}
      onDragLeave={() => {
        setDragging(false);
      }}
      onDrop={onDrop}
    >
      {descriptionId !== null && (
        <p id={descriptionId} className="max-w-prose text-sm leading-relaxed text-fg-muted">
          {description}
        </p>
      )}
      {errorId !== null && <FieldError id={errorId} message={error ?? ""} />}
      <div className="flex flex-wrap items-center gap-3">
        <label className={`file-picker relative ${buttonClassName({ variant: "secondary" })}`}>
          <Upload aria-hidden="true" className="size-4" />
          {label}
          <input
            id={id}
            type="file"
            multiple
            className="sr-only"
            aria-describedby={describedBy.join(" ")}
            {...(errorId === null ? {} : { "aria-invalid": true })}
            {...(accept === undefined ? {} : { accept })}
            onChange={onChange}
          />
        </label>
        <p id={statusId} className="text-sm text-fg-muted">
          {TEXT.workbench.filesChosen(chosenCount)}
        </p>
      </div>
      {dragging && (
        // Só quem arrasta com o mouse vê a dica; o leitor de tela usa o input.
        <p aria-hidden="true" className="font-semibold text-fg">
          {TEXT.workbench.dropHere}
        </p>
      )}
    </div>
  );
}

function carriesFiles(event: DragEvent<HTMLElement>): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}
