import { FolderOpen, Upload } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactElement,
} from "react";

import { TEXT } from "../../text";
import {
  collectEntries,
  folderEntries,
  pickFromFolderInput,
  supportsFolderPicker,
  type FolderPick,
} from "../../workbench/folderFiles";
import { buttonClassName } from "../ui/Button";
import { FieldError } from "./Field";
import { LiveRegion } from "./LiveRegion";

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
  /**
   * Pasta inteira: o botão "Escolher pasta" (onde o navegador tem `webkitdirectory`) e a pasta
   * solta sobre a área chegam em `onPick` já filtradas e conferidas contra `maxFiles`.
   */
  readonly folder?: {
    readonly maxFiles: number;
    readonly onPick: (pick: FolderPick) => void;
  };
  /** Resultado da última escolha (por exemplo, o que ficou de fora), anunciado e visível. */
  readonly notice?: string | null;
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
  folder,
  notice = null,
}: FileDropzoneProps): ReactElement {
  const [dragging, setDragging] = useState(false);
  const [folderPicker] = useState(supportsFolderPicker);
  const folderInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);
  const latestFolder = useRef(folder);
  useEffect(() => {
    latestFolder.current = folder;
  });
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // O React não conhece o atributo `webkitdirectory`; a propriedade do DOM é a mesma coisa.
  useEffect(() => {
    if (folderInput.current !== null) {
      folderInput.current.webkitdirectory = true;
    }
  });
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

  const onFolderChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []);
    input.value = "";
    if (folder !== undefined && files.length > 0) {
      folder.onPick(pickFromFolderInput(files, folder.maxFiles));
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
    // Os itens só valem durante o evento: as entradas saem daqui antes de qualquer espera.
    const roots = folder === undefined ? null : folderEntries(event.dataTransfer.items);
    if (folder !== undefined && roots !== null) {
      void collectEntries(roots, folder.maxFiles).then((pick) => {
        // O poço pode ter saído enquanto a pasta era lida: nada de envio de uma tela que já fechou.
        if (mounted.current) {
          latestFolder.current?.onPick(pick);
        }
      });
      return;
    }
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) {
      onFiles(files);
    }
  };

  return (
    // Soltar arquivos é só um atalho do mouse; o controle acessível é o input de dentro, então a
    // área não precisa de papel nem de teclado próprios.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- soltar é atalho; o controle é o input
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
        {folder !== undefined && folderPicker && (
          <label className={`file-picker relative ${buttonClassName({ variant: "secondary" })}`}>
            <FolderOpen aria-hidden="true" className="size-4" />
            {TEXT.workbench.chooseFolder}
            <input
              ref={folderInput}
              id={`${id}-pasta`}
              type="file"
              multiple
              className="sr-only"
              aria-describedby={describedBy.join(" ")}
              onChange={onFolderChange}
            />
          </label>
        )}
        <p id={statusId} className="text-sm text-fg-muted">
          {TEXT.workbench.filesChosen(chosenCount)}
        </p>
      </div>
      <div className="text-sm text-fg">
        <LiveRegion message={notice} />
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
