// Cabeçalhos da SPA lidos do snippet de produção do Nginx, para o servidor do E2E servir
// exatamente a mesma CSP e o csp.spec.ts não testar uma cópia que envelhece.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** O snippet incluído em cada `location` de estáticos do default.conf.template. */
const SPA_HEADERS_SNIPPET = fileURLToPath(
  new URL("../../../docker/nginx/snippets/spa-headers.conf", import.meta.url),
);

const ADD_HEADER =
  /^add_header\s+(?<name>[\w-]+)\s+(?:"(?<quoted>[^"]*)"|(?<bare>\S+))(?:\s+always)?;$/;

/**
 * Cada `add_header` do snippet, na ordem, como nome → valor.
 *
 * Valor que é variável do Nginx (`$hsts`) fica de fora: ele depende da requisição, e por HTTP sai
 * vazio, o que faz o Nginx não emitir o cabeçalho. Linha `add_header` que este parser não entende
 * lança, para uma mudança no snippet não sumir em silêncio com um cabeçalho de segurança.
 */
function parseSpaHeaders(snippet: string): ReadonlyMap<string, string> {
  const headers = new Map<string, string>();
  for (const rawLine of snippet.split("\n")) {
    const line = rawLine.trim();
    if (!line.startsWith("add_header")) {
      continue;
    }
    const groups = ADD_HEADER.exec(line)?.groups;
    if (groups === undefined) {
      throw new Error(`add_header que o E2E não sabe ler: ${line}`);
    }
    const value = groups["quoted"] ?? groups["bare"] ?? "";
    if (!value.startsWith("$")) {
      headers.set(groups["name"] ?? "", value);
    }
  }
  return headers;
}

export function readSpaHeaders(): ReadonlyMap<string, string> {
  return parseSpaHeaders(readFileSync(SPA_HEADERS_SNIPPET, "utf8"));
}

/** A CSP de produção; lança se o snippet não a tiver. */
export function productionCsp(): string {
  const csp = readSpaHeaders().get("Content-Security-Policy");
  if (csp === undefined) {
    throw new Error(`o snippet ${SPA_HEADERS_SNIPPET} não define Content-Security-Policy`);
  }
  return csp;
}
