/**
 * Dublês das entradas do File and Directory Entries API (`webkitGetAsEntry`), que o jsdom não tem.
 * A pasta devolve os lotes do `readEntries` na ordem dada e depois um lote vazio, como o navegador,
 * que entrega no máximo 100 entradas por chamada.
 */

interface FakeEntryBase {
  readonly name: string;
  readonly isFile: boolean;
  readonly isDirectory: boolean;
}

export interface FakeFileEntry extends FakeEntryBase {
  file(success: (file: File) => void, failure?: (error: DOMException) => void): void;
}

export interface FakeDirectoryEntry extends FakeEntryBase {
  createReader(): {
    readEntries(
      success: (entries: FakeEntry[]) => void,
      failure?: (error: DOMException) => void,
    ): void;
  };
  /** Quantas chamadas do `readEntries` devolveram entradas. */
  batchesRead(): number;
}

export type FakeEntry = FakeFileEntry | FakeDirectoryEntry | FakeEntryBase;

export function fakeFile(name: string, content = "x"): FakeFileEntry {
  return {
    name,
    isFile: true,
    isDirectory: false,
    file: (success) => {
      success(new File([content], name));
    },
  };
}

/** Arquivo que o navegador lista mas não deixa ler (sem permissão, sumiu no meio). */
export function fakeUnreadable(name: string): FakeFileEntry {
  return {
    name,
    isFile: true,
    isDirectory: false,
    file: (_success, failure) => {
      failure?.(new DOMException("não lido", "NotReadableError"));
    },
  };
}

/** Entrada que não é arquivo regular nem pasta (FIFO, socket, dispositivo). */
export function fakeSpecial(name: string): FakeEntryBase {
  return { name, isFile: false, isDirectory: false };
}

/** Pasta com os lotes do `readEntries`; `"erro"` faz a leitura falhar. */
export function fakeDir(
  name: string,
  batches: readonly FakeEntry[][] | "erro",
): FakeDirectoryEntry {
  let read = 0;
  return {
    name,
    isFile: false,
    isDirectory: true,
    batchesRead: () => read,
    createReader: () => {
      let next = 0;
      return {
        readEntries: (success, failure) => {
          if (batches === "erro") {
            failure?.(new DOMException("não lido", "NotReadableError"));
            return;
          }
          const batch = batches[next] ?? [];
          next += 1;
          if (batch.length > 0) {
            read += 1;
          }
          success(batch);
        },
      };
    },
  };
}

/** `count` arquivos `<prefixo><n>.txt`. */
export function numbered(count: number, prefix = "f"): FakeFileEntry[] {
  return Array.from({ length: count }, (_, index) => fakeFile(`${prefix}${String(index)}.txt`));
}

/** Item de `DataTransfer.items` que devolve a entrada dada. */
export function fakeItem(entry: FakeEntry): {
  readonly kind: "file";
  webkitGetAsEntry(): FakeEntry;
} {
  return { kind: "file", webkitGetAsEntry: () => entry };
}
