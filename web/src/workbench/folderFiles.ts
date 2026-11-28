/**
 * Arquivos de uma pasta inteira: escolhida pelo "Escolher pasta" (`webkitdirectory`), solta sobre
 * o poço ou colada. Vale o teto de entradas por execução: acima dele nada é enviado, porque enviar
 * só parte da pasta em silêncio deixaria a pessoa sem saber o que ficou de fora. O nome enviado é
 * sempre `file.name`, nunca o caminho relativo: nome é dado, não caminho (o servidor recusa `/`).
 */

export interface FolderPick {
  readonly files: readonly File[];
  /** Ocultos e de sistema (`.DS_Store`, `Thumbs.db`...); pasta oculta conta como um item. */
  readonly skippedHidden: number;
  /** Entradas que não são arquivo regular nem pasta, ou que o navegador não deixou ler. */
  readonly skippedUnreadable: number;
  /** Passou do teto: `files` vem vazio e nada deve ser enviado. */
  readonly overflow: boolean;
}

/** O mínimo de um `FileSystemEntry` que a leitura usa. */
interface Entry {
  readonly name: string;
  readonly isFile: boolean;
  readonly isDirectory: boolean;
}

interface FileEntry extends Entry {
  file(success: (file: File) => void, failure?: (error: DOMException) => void): void;
}

interface DirectoryEntry extends Entry {
  createReader(): {
    readEntries(success: (entries: Entry[]) => void, failure?: (error: DOMException) => void): void;
  };
}

interface EntryItem {
  readonly kind: string;
  webkitGetAsEntry(): Entry | null;
}

/**
 * Teto de entradas visitadas, pastas inclusive. O teto de arquivos já para a leitura cedo; este
 * segura uma árvore de pastas quase vazias (soltar a pasta pessoal não enumera o disco).
 */
const MAX_ENTRIES_VISITED = 10_000;

const SYSTEM_NAMES = new Set(["thumbs.db", "desktop.ini"]);

const byName = new Intl.Collator("pt-BR", { numeric: true, sensitivity: "base" });

export function isHiddenOrSystem(name: string): boolean {
  return name.startsWith(".") || SYSTEM_NAMES.has(name.toLowerCase());
}

export function supportsFolderPicker(): boolean {
  return "webkitdirectory" in HTMLInputElement.prototype;
}

/**
 * As entradas de um `DataTransfer` quando alguma delas é pasta; `null` quando não há pasta, e os
 * arquivos soltos seguem pelo `files` de sempre. Precisa rodar dentro do evento: depois dele o
 * navegador esvazia a lista de itens.
 */
export function folderEntries(items: ArrayLike<EntryItem>): Entry[] | null {
  const entries = Array.from(items).flatMap((item) => {
    const entry = item.kind === "file" ? item.webkitGetAsEntry() : null;
    return entry === null ? [] : [entry];
  });
  return entries.some((entry) => entry.isDirectory) ? entries : null;
}

/**
 * Percorre as entradas em profundidade, cada nível na ordem do nome, e lê os arquivos. A pasta
 * solta em si nunca vira arquivo (no Firefox e no Safari ela também chega em `files` como um
 * `File` de 0 B). Para assim que passa de `maxFiles` arquivos.
 */
export async function collectEntries(
  roots: readonly Entry[],
  maxFiles: number,
): Promise<FolderPick> {
  const walk = newWalk(maxFiles);
  const overflow = await visit(sorted(roots), walk, true);
  return finish(walk, overflow);
}

export function pickFromFolderInput(files: ArrayLike<File>, maxFiles: number): FolderPick {
  const walk = newWalk(maxFiles);
  const ordered = Array.from(files).sort((a, b) => byName.compare(pathOf(a), pathOf(b)));
  for (const file of ordered) {
    // A primeira parte do caminho é a pasta escolhida, que a pessoa nomeou de propósito.
    if (pathOf(file).split("/").slice(1).some(isHiddenOrSystem)) {
      walk.skippedHidden += 1;
    } else {
      walk.files.push(file);
    }
  }
  return finish(walk, walk.files.length > maxFiles);
}

interface Walk {
  readonly maxFiles: number;
  files: File[];
  skippedHidden: number;
  skippedUnreadable: number;
  visited: number;
}

function newWalk(maxFiles: number): Walk {
  return { maxFiles, files: [], skippedHidden: 0, skippedUnreadable: 0, visited: 0 };
}

function finish(walk: Walk, overflow: boolean): FolderPick {
  return {
    files: overflow ? [] : walk.files,
    skippedHidden: walk.skippedHidden,
    skippedUnreadable: walk.skippedUnreadable,
    overflow,
  };
}

/** Visita as entradas; devolve `true` quando o teto estourou e a leitura parou. */
async function visit(entries: readonly Entry[], walk: Walk, root = false): Promise<boolean> {
  for (const entry of entries) {
    walk.visited += 1;
    if (walk.visited > MAX_ENTRIES_VISITED) {
      return true;
    }
    // A pasta solta ou colada a pessoa nomeou de propósito; dentro dela vale o filtro.
    if (!(root && entry.isDirectory) && isHiddenOrSystem(entry.name)) {
      walk.skippedHidden += 1;
    } else if (isDirectory(entry)) {
      const children = await readChildren(entry, walk.maxFiles - walk.files.length);
      if (children === "overflow") {
        return true;
      }
      if (children === null) {
        walk.skippedUnreadable += 1;
      } else if (await visit(sorted(children), walk)) {
        return true;
      }
    } else if (isFile(entry)) {
      const file = await readFile(entry);
      if (file === null) {
        walk.skippedUnreadable += 1;
      } else {
        walk.files.push(file);
        if (walk.files.length > walk.maxFiles) {
          return true;
        }
      }
    } else {
      walk.skippedUnreadable += 1;
    }
  }
  return false;
}

/**
 * Todas as entradas da pasta: o `readEntries` devolve no máximo 100 por chamada e é repetido até
 * vir vazio. `null` quando a pasta não abre; `"overflow"` assim que só os arquivos desta pasta já
 * passam de `fileBudget`, sem ler os lotes seguintes.
 */
async function readChildren(
  directory: DirectoryEntry,
  fileBudget: number,
): Promise<Entry[] | null | "overflow"> {
  const reader = directory.createReader();
  const children: Entry[] = [];
  let files = 0;
  for (;;) {
    const batch = await new Promise<Entry[] | null>((resolve) => {
      reader.readEntries(resolve, () => {
        resolve(null);
      });
    });
    if (batch === null) {
      return null;
    }
    if (batch.length === 0 || children.push(...batch) > MAX_ENTRIES_VISITED) {
      return children;
    }
    files += batch.filter((entry) => entry.isFile && !isHiddenOrSystem(entry.name)).length;
    if (files > fileBudget) {
      return "overflow";
    }
  }
}

function readFile(entry: FileEntry): Promise<File | null> {
  return new Promise((resolve) => {
    entry.file(resolve, () => {
      resolve(null);
    });
  });
}

function isDirectory(entry: Entry): entry is DirectoryEntry {
  return entry.isDirectory && "createReader" in entry;
}

function isFile(entry: Entry): entry is FileEntry {
  return entry.isFile && "file" in entry;
}

function sorted(entries: readonly Entry[]): Entry[] {
  return [...entries].sort((a, b) => byName.compare(a.name, b.name));
}

function pathOf(file: File): string {
  return file.webkitRelativePath === "" ? file.name : file.webkitRelativePath;
}
