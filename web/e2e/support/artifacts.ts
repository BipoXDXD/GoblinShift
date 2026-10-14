// Artefatos da API falsa em disco, para o servidor local entregá-los como download de verdade.
// Formato: <dir>/<id> com o corpo e <dir>/<id>.json com nome e tipo.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ARTIFACT_ID = /^[0-9a-f-]{36}$/;
const DOWNLOAD_PATH = /^\/api\/v1\/artifacts\/(?<id>[0-9a-f-]{36})\/download$/;

/** Id do artefato de um caminho de download da API, ou `null` para qualquer outro caminho. */
export function artifactIdOfDownload(path: string): string | null {
  return DOWNLOAD_PATH.exec(path)?.groups?.["id"] ?? null;
}

export interface ArtifactMeta {
  readonly fileName: string;
  readonly contentType: string;
}

export function writeArtifact(
  dir: string,
  artifactId: string,
  artifact: ArtifactMeta & { readonly body: Buffer },
): void {
  writeFileSync(join(dir, artifactId), artifact.body);
  const meta: ArtifactMeta = { fileName: artifact.fileName, contentType: artifact.contentType };
  writeFileSync(join(dir, `${artifactId}.json`), JSON.stringify(meta));
}

/** Metadados e caminho do corpo, ou `null` para id malformado ou inexistente. */
export function readArtifactMeta(
  dir: string,
  artifactId: string,
): (ArtifactMeta & { readonly bodyPath: string }) | null {
  if (!ARTIFACT_ID.test(artifactId)) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(readFileSync(join(dir, `${artifactId}.json`), "utf8"));
    if (!isArtifactMeta(parsed)) {
      return null;
    }
    return { ...parsed, bodyPath: join(dir, artifactId) };
  } catch {
    // Arquivo ausente: o artefato não existe, e o servidor responde 404.
    return null;
  }
}

function isArtifactMeta(value: unknown): value is ArtifactMeta {
  return (
    typeof value === "object" &&
    value !== null &&
    "fileName" in value &&
    typeof value.fileName === "string" &&
    "contentType" in value &&
    typeof value.contentType === "string"
  );
}
