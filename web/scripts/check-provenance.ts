// Proveniência da arte do site (ADR 030): todo arquivo de src/assets/art/ tem entrada no
// PROVENANCE.json com o sha256 certo, a cadeia de gerações até a ferramenta resolve, cada geração
// cita um direito registrado e nada deriva de uma goblin excluída por vir de imagem de terceiros.
// Roda no job `web` do CI: `node scripts/check-provenance.ts` sai com 1 se algo falhar.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { z } from "zod";

export interface ArtFile {
  /** Caminho relativo a `src/assets/art/`, como `goblins/lua-128w.avif`. */
  readonly path: string;
  readonly sha256: string;
}

const generationSchema = z.object({
  inputs: z.array(z.string()),
  rights: z.string().optional(),
  generation_id: z.string().optional(),
});

const provenanceSchema = z.object({
  schema: z.literal(1),
  rights: z.array(z.object({ id: z.string() })),
  generations: z.record(z.string(), generationSchema),
  excluded: z.array(z.object({ name: z.string(), rodin: z.string(), reason: z.string() })),
  assets: z.array(z.object({ file: z.string(), sha256: z.string(), derived_from: z.string() })),
});

type Provenance = z.infer<typeof provenanceSchema>;

const MANIFEST = "PROVENANCE.json";

/** Cada problema em texto para o log do CI; lista vazia quando está tudo certo. */
export function provenanceViolations(files: readonly ArtFile[], document: unknown): string[] {
  const parsed = provenanceSchema.safeParse(document);
  if (!parsed.success) {
    return [`${MANIFEST} fora do formato esperado`];
  }
  const provenance = parsed.data;
  const onDisk = new Map(files.map((file) => [file.path, file.sha256]));
  const listed = new Set(provenance.assets.map((asset) => asset.file));
  const violations: string[] = files
    .filter((file) => !listed.has(file.path))
    .map((file) => `${file.path}: sem entrada no ${MANIFEST}`);
  for (const asset of provenance.assets) {
    const sha256 = onDisk.get(asset.file);
    if (sha256 === undefined) {
      violations.push(`${asset.file}: está no ${MANIFEST}, mas o arquivo não existe`);
      continue;
    }
    if (sha256 !== asset.sha256) {
      violations.push(`${asset.file}: sha256 diferente do registrado`);
    }
    violations.push(
      ...chainViolations(provenance, asset.derived_from).map((v) => `${asset.file}: ${v}`),
    );
  }
  return violations;
}

/** Percorre a cadeia de gerações a partir de `start`, uma vez por geração. */
function chainViolations(provenance: Provenance, start: string): string[] {
  const rightIds = new Set(provenance.rights.map((right) => right.id));
  const violations: string[] = [];
  const seen = new Set<string>();
  const pending = [start];
  for (let id = pending.pop(); id !== undefined; id = pending.pop()) {
    if (seen.has(id)) {
      continue;
    }
    seen.add(id);
    const generation = provenance.generations[id];
    if (generation === undefined) {
      violations.push(`geração ${id} não registrada`);
      continue;
    }
    if (generation.rights !== undefined && !rightIds.has(generation.rights)) {
      violations.push(`${id} cita o direito ${generation.rights}, que não está em rights`);
    }
    const excluded = provenance.excluded.find((entry) => generation.generation_id === entry.rodin);
    if (excluded !== undefined) {
      violations.push(
        `deriva de ${excluded.rodin} (${excluded.name}), excluída: ${excluded.reason}`,
      );
    }
    pending.push(...generation.inputs);
  }
  return violations;
}

function artFiles(dir: string): ArtFile[] {
  return readdirSync(dir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name !== MANIFEST)
    .map((entry) => {
      const full = join(entry.parentPath, entry.name);
      return {
        path: relative(dir, full),
        sha256: createHash("sha256").update(readFileSync(full)).digest("hex"),
      };
    });
}

function main(): void {
  const dir = fileURLToPath(new URL("../src/assets/art", import.meta.url));
  const document: unknown = JSON.parse(readFileSync(join(dir, MANIFEST), "utf8"));
  const violations = provenanceViolations(artFiles(dir), document);
  for (const violation of violations) {
    console.error(violation);
  }
  if (violations.length > 0) {
    process.exitCode = 1;
    return;
  }
  console.log("Proveniência da arte: todos os arquivos registrados e com cadeia resolvida.");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main();
}
