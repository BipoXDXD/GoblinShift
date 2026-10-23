// Proveniência da arte do site (ADR 030): todo arquivo de src/assets/art/ e toda imagem de public/
// (ícones e imagem de compartilhamento, com o prefixo `public/`) tem entrada no
// PROVENANCE.json com o sha256 certo, a cadeia de gerações até a ferramenta resolve, cada geração
// cita um direito registrado que bate com o upstream declarado, nada deriva de uma goblin excluída
// e geração de origem de terceiros (ou não verificável) só passa com decisão registrada em
// `exceptions`: quem, quando e por quê.
// Roda no job `web` do CI: `node scripts/check-provenance.ts` sai com 1 se algo falhar.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { z } from "zod";

export interface ArtFile {
  /** Caminho relativo a `src/assets/art/`, como `goblins/nyx-128w.avif`. */
  readonly path: string;
  readonly sha256: string;
}

const generationSchema = z.object({
  inputs: z.array(z.string()),
  rights: z.string().optional(),
  generation_id: z.string().optional(),
  /** Identificador neutro da origem, vinculado ao registro externo. */
  upstream: z.string().optional(),
  /** Por que a origem é de terceiros ou não verificável; ausente quando é nossa. */
  third_party: z.string().optional(),
});

const exceptionSchema = z.object({
  generation: z.string(),
  decided_by: z.string().min(1),
  decided_at: z.iso.date(),
  reason: z.string().min(1),
});

const provenanceSchema = z.object({
  schema: z.literal(1),
  rights: z.array(
    z.object({
      id: z.string(),
      /** Trecho que o `upstream` das gerações que citam este direito precisa conter. */
      upstream: z.string().optional(),
    }),
  ),
  generations: z.record(z.string(), generationSchema),
  excluded: z.array(z.object({ name: z.string(), source_id: z.string(), reason: z.string() })),
  exceptions: z.array(exceptionSchema).default([]),
  assets: z.array(z.object({ file: z.string(), sha256: z.string(), derived_from: z.string() })),
});

type Provenance = z.infer<typeof provenanceSchema>;

const MANIFEST = "PROVENANCE.json";
const PUBLIC_PREFIX = "public/";
const IMAGE = /\.(avif|ico|jpe?g|png|svg|webp)$/;

/** Imagem de `public/` (ícones, imagem de compartilhamento): também é arte do site. */
export function isPublicArt(path: string): boolean {
  return IMAGE.test(path);
}

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
  const rightsById = new Map(provenance.rights.map((right) => [right.id, right]));
  const decided = new Set(provenance.exceptions.map((exception) => exception.generation));
  const violations: string[] = [];
  const seen = new Set<string>();
  const active = new Set<string>();
  const pending: { id: string; exit: boolean }[] = [{ id: start, exit: false }];
  for (let frame = pending.pop(); frame !== undefined; frame = pending.pop()) {
    const { id, exit } = frame;
    if (exit) {
      active.delete(id);
      seen.add(id);
      continue;
    }
    if (active.has(id)) {
      violations.push(`ciclo na origem ${id}`);
      continue;
    }
    if (seen.has(id)) {
      continue;
    }
    active.add(id);
    pending.push({ id, exit: true });
    const generation = provenance.generations[id];
    if (generation === undefined) {
      violations.push(`geração ${id} não registrada`);
      continue;
    }
    if (generation.rights !== undefined && !rightsById.has(generation.rights)) {
      violations.push(`${id} cita o direito ${generation.rights}, que não está em rights`);
    }
    violations.push(...upstreamViolations(id, generation, rightsById));
    if (generation.third_party !== undefined && !decided.has(id)) {
      violations.push(
        `deriva de ${id} (${generation.third_party}) sem decisão registrada em exceptions`,
      );
    }
    const excluded = provenance.excluded.find(
      (entry) => generation.generation_id === entry.source_id,
    );
    if (excluded !== undefined) {
      violations.push(
        `deriva de ${excluded.source_id} (${excluded.name}), excluída: ${excluded.reason}`,
      );
    }
    pending.push(...generation.inputs.map((inputId) => ({ id: inputId, exit: false })));
  }
  return violations;
}

/** O direito citado precisa ser o do upstream declarado: cada fornecedor tem termos próprios. */
function upstreamViolations(
  id: string,
  generation: Provenance["generations"][string],
  rightsById: ReadonlyMap<string, Provenance["rights"][number]>,
): string[] {
  const right = generation.rights === undefined ? undefined : rightsById.get(generation.rights);
  if (generation.rights !== undefined && right === undefined) {
    return []; // direito inexistente: já apontado
  }
  if (generation.upstream !== undefined) {
    if (right === undefined) {
      return [`${id} tem upstream "${generation.upstream}", mas não cita direito`];
    }
    if (right.upstream === undefined) {
      return [
        `${id} tem upstream "${generation.upstream}", mas cita o direito ${right.id}, que não declara upstream`,
      ];
    }
    if (generation.upstream !== right.upstream) {
      return [
        `${id} tem upstream "${generation.upstream}", mas cita o direito ${right.id}, que vale para "${right.upstream}"`,
      ];
    }
    return [];
  }
  if (right?.upstream !== undefined) {
    return [
      `${id} cita o direito ${right.id}, que vale para "${right.upstream}", mas não declara upstream`,
    ];
  }
  return [];
}

function filesIn(dir: string): ArtFile[] {
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
  const publicDir = fileURLToPath(new URL("../public", import.meta.url));
  const publicArt = filesIn(publicDir)
    .filter((file) => isPublicArt(file.path))
    .map((file) => ({ ...file, path: PUBLIC_PREFIX + file.path }));
  const document: unknown = JSON.parse(readFileSync(join(dir, MANIFEST), "utf8"));
  const violations = provenanceViolations([...filesIn(dir), ...publicArt], document);
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
