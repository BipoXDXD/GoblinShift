// Caixa de entrada do compartilhamento (ADR 062): o service worker guarda aqui os arquivos que
// chegam pelo `POST /compartilhar` do `share_target`, e a página os lê depois, uma vez só. Sem
// `self` nem `window`: o mesmo código roda no service worker e na página, e o teste usa um
// `CacheStorage` falso.

/** Nome do cache da Cache API com os arquivos compartilhados. */
export const SHARE_CACHE = "gs-share";

/** Validade de um compartilhamento: depois de 1 h, os arquivos são apagados sem uso. */
export const SHARE_TTL_MS = 3_600_000;

/** Campo do formulário do `share_target` (manifest.webmanifest) com os arquivos. */
export const SHARE_FIELD = "arquivos";

const PREFIX = "/__compartilhado/";
const NAME_HEADER = "X-Gs-Name";
const AT_HEADER = "X-Gs-At";

interface Entry {
  readonly request: Request;
  readonly index: number;
  readonly response: Response;
}

/**
 * Grava cada arquivo do campo `arquivos` sob um id novo e devolve o id. Antes, apaga as entradas
 * vencidas de compartilhamentos que ninguém abriu.
 */
export async function storeShared(
  caches: CacheStorage,
  form: FormData,
  { now, newId }: { readonly now: number; readonly newId: () => string },
): Promise<string> {
  const cache = await caches.open(SHARE_CACHE);
  await purgeExpired(cache, now);
  const id = newId();
  const files = form.getAll(SHARE_FIELD).filter((value) => value instanceof File);
  await Promise.all(
    files.map((file, index) =>
      cache.put(
        entryPath(id, index),
        new Response(file, {
          headers: {
            [NAME_HEADER]: encodeURIComponent(file.name),
            "Content-Type": file.type === "" ? "application/octet-stream" : file.type,
            [AT_HEADER]: String(now),
          },
        }),
      ),
    ),
  );
  return id;
}

/** Os arquivos do compartilhamento, que saem da caixa; sem entrada ou vencido, `"expired"`. */
export async function takeShared(
  caches: CacheStorage,
  id: string,
  now: number,
): Promise<readonly File[] | "expired"> {
  const cache = await caches.open(SHARE_CACHE);
  const entries = await entriesOf(cache, id);
  const valid = entries.length > 0 && entries.every((entry) => !isExpired(entry.response, now));
  const files = valid ? await Promise.all(entries.map(fileOf)) : null;
  await Promise.all(entries.map((entry) => cache.delete(entry.request)));
  return files ?? "expired";
}

/** Só os nomes, sem tirar da caixa: a escolha da receita (SharePage) vem antes da Bancada. */
export async function peekSharedNames(
  caches: CacheStorage,
  id: string,
  now: number,
): Promise<readonly string[] | "expired"> {
  const cache = await caches.open(SHARE_CACHE);
  const entries = await entriesOf(cache, id);
  if (entries.length === 0 || entries.some((entry) => isExpired(entry.response, now))) {
    return "expired";
  }
  return entries.map((entry) => nameOf(entry.response));
}

function entryPath(id: string, index: number): string {
  return `${PREFIX}${encodeURIComponent(id)}/${String(index)}`;
}

/** As entradas do id, na ordem em que os arquivos chegaram. */
async function entriesOf(cache: Cache, id: string): Promise<readonly Entry[]> {
  const prefix = `${PREFIX}${encodeURIComponent(id)}/`;
  const requests = await cache.keys();
  const entries = await Promise.all(
    requests.map(async (request): Promise<Entry | null> => {
      const { pathname } = new URL(request.url);
      const index = Number(pathname.slice(prefix.length));
      if (!pathname.startsWith(prefix) || !Number.isInteger(index)) {
        return null;
      }
      const response = await cache.match(request);
      return response === undefined ? null : { request, index, response };
    }),
  );
  return entries.filter((entry) => entry !== null).sort((a, b) => a.index - b.index);
}

async function purgeExpired(cache: Cache, now: number): Promise<void> {
  const requests = await cache.keys();
  await Promise.all(
    requests.map(async (request) => {
      const response = await cache.match(request);
      if (response === undefined || isExpired(response, now)) {
        await cache.delete(request);
      }
    }),
  );
}

/** Sem a hora da gravação, a entrada não é confiável e conta como vencida. */
function isExpired(response: Response, now: number): boolean {
  const at = Number(response.headers.get(AT_HEADER) ?? Number.NaN);
  return !Number.isFinite(at) || now - at > SHARE_TTL_MS;
}

function nameOf(response: Response): string {
  return decodeURIComponent(response.headers.get(NAME_HEADER) ?? "");
}

async function fileOf({ response }: Entry): Promise<File> {
  const type = response.headers.get("Content-Type") ?? "";
  return new File([await response.blob()], nameOf(response), {
    type: type === "application/octet-stream" ? "" : type,
  });
}
