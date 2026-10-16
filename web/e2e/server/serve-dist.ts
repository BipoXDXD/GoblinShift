// Servidor do build da SPA para o E2E no CI: o mesmo roteamento e os mesmos cabeçalhos do
// default.conf.template (location / e /assets/), com a CSP lida do snippet de produção.
//
// O backend não passa por aqui: no modo local a API é falsa, dentro do processo do Playwright
// (support/fakeApi.ts). Pedido de API que chegar até este servidor responde 502, para o teste
// falhar alto em vez de seguir com uma resposta inventada.
//
// A exceção são os downloads (GET /api/v1/artifacts/{id}/download): o Chromium cancela o download
// de uma resposta montada pelo route.fulfill ou desviada para outra URL, então a API falsa grava o
// artefato em --artifacts e deixa o pedido seguir até aqui, já autenticado por ela.
//
// Uso: node e2e/server/serve-dist.ts --port 4173 --dist dist --artifacts /tmp/x
import { createReadStream, statSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { extname, join, resolve, sep } from "node:path";
import { parseArgs } from "node:util";

import { artifactIdOfDownload, readArtifactMeta } from "../support/artifacts.ts";
import { readSpaHeaders } from "../support/nginxHeaders.ts";

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "4173" },
    dist: { type: "string", default: "dist" },
    artifacts: { type: "string" },
  },
});

const DIST_DIR = resolve(values.dist);
const PORT = Number(values.port);
const ARTIFACTS_DIR = values.artifacts === undefined ? null : resolve(values.artifacts);
const SPA_HEADERS = readSpaHeaders();

// Mesmos tipos do mime.types da imagem do Nginx para o que o build do Vite gera.
const CONTENT_TYPES: Readonly<Record<string, string>> = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".avif": "image/avif",
  ".woff2": "font/woff2",
  ".glb": "model/gltf-binary",
  ".ico": "image/x-icon",
  ".txt": "text/plain",
  // A location = /manifest.webmanifest define o tipo: o mime.types da imagem não o conhece.
  ".webmanifest": "application/manifest+json",
};

/** Ícones, manifest e imagem de compartilhamento na raiz: nome fixo e 404 se faltar. */
const BRAND_FILE = /^\/[^/]+\.(?:png|jpg|ico|svg|webmanifest)$/;

const BACKEND_PREFIXES = ["/api/", "/auth/"];
const BACKEND_PATHS = new Set(["/docs", "/openapi.json", "/livez", "/readyz"]);

const server = createServer((request, response) => {
  const path = pathOf(request);
  if (path === null) {
    response.writeHead(400).end();
    return;
  }
  const artifactId = artifactIdOfDownload(path);
  if (artifactId !== null && ARTIFACTS_DIR !== null) {
    sendArtifact(response, ARTIFACTS_DIR, artifactId);
    return;
  }
  if (isBackend(path)) {
    response
      .writeHead(502, { "Content-Type": "text/plain" })
      .end("O E2E local não tem backend; a API falsa roda no Playwright.");
    return;
  }
  if (path.startsWith("/assets/")) {
    // location /assets/: try_files $uri =404, cache imutável.
    sendFile(response, fileIn(path), "public, max-age=31536000, immutable");
    return;
  }
  if (BRAND_FILE.test(path)) {
    // location ~ ^/[^/]+\.(png|jpg|ico|svg)$ e = /manifest.webmanifest: try_files $uri =404.
    sendFile(response, fileIn(path), "no-cache");
    return;
  }
  // location /: try_files $uri /index.html, sem cache.
  sendFile(response, fileIn(path) ?? join(DIST_DIR, "index.html"), "no-cache");
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`SPA de ${DIST_DIR} em http://127.0.0.1:${String(PORT)}`);
});

function pathOf(request: IncomingMessage): string | null {
  try {
    return decodeURIComponent(new URL(request.url ?? "/", "http://localhost").pathname);
  } catch {
    // URL malformada (%E0%A4%A): o Nginx também responde 400.
    return null;
  }
}

function isBackend(path: string): boolean {
  return BACKEND_PATHS.has(path) || BACKEND_PREFIXES.some((prefix) => path.startsWith(prefix));
}

/** Arquivo regular dentro de dist/ para o caminho, ou `null`; nunca sai de dist/. */
function fileIn(path: string): string | null {
  const file = resolve(DIST_DIR, `.${path}`);
  if (file !== DIST_DIR && !file.startsWith(DIST_DIR + sep)) {
    return null;
  }
  try {
    return statSync(file).isFile() ? file : null;
  } catch {
    // Inexistente: o chamador decide entre 404 e o index.html, como o try_files.
    return null;
  }
}

function sendFile(response: ServerResponse, file: string | null, cacheControl: string): void {
  const headers: Record<string, string> = { ...Object.fromEntries(SPA_HEADERS) };
  headers["Cache-Control"] = cacheControl;
  if (file === null) {
    response.writeHead(404, { ...headers, "Content-Type": "text/html" }).end("Not Found");
    return;
  }
  headers["Content-Type"] = CONTENT_TYPES[extname(file)] ?? "application/octet-stream";
  response.writeHead(200, headers);
  createReadStream(file).pipe(response);
}

function sendArtifact(response: ServerResponse, dir: string, artifactId: string): void {
  const artifact = readArtifactMeta(dir, artifactId);
  if (artifact === null) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, {
    "Content-Type": artifact.contentType,
    "Content-Disposition": `attachment; filename="${artifact.fileName}"`,
  });
  createReadStream(artifact.bodyPath).pipe(response);
}
