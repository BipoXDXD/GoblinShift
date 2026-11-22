// O elenco do palco 3D (ADR 038): as seis goblins, cada uma com a imagem estática que aparece
// antes e no lugar da cena, e que também é a miniatura do seletor.
import { TEXT } from "../text";

export type CastId = keyof typeof TEXT.art.goblins;

/** A ordem do seletor é a do elenco nos créditos: a Rúbia, mascote da abertura, primeiro. */
export const CAST_IDS: readonly CastId[] = ["rubia", "fagulha", "ferrugem", "jade", "lua", "nyx"];

/** A Rúbia abre a página: é a única cena que a home carrega sem a pessoa pedir. */
export const FIRST_ON_STAGE: CastId = "rubia";

/** Larguras geradas por scripts/arte/gerar-imagens.sh para a cena. */
const WIDTHS = [192, 384, 640] as const;
const FALLBACK_WIDTH = 640;
const THUMBNAIL_WIDTH = 192;

const FILES = import.meta.glob<string>("../assets/art/oficina-3d/*.{avif,webp}", {
  eager: true,
  import: "default",
});

export interface CastMember {
  readonly id: CastId;
  readonly name: string;
  readonly alt: string;
  /** `srcset` com descritor de largura, um por formato. */
  readonly avif: string;
  readonly webp: string;
  /** WebP de 640 px, para navegador sem `<picture>`. */
  readonly fallback: string;
  /** A menor imagem, para a miniatura do botão. */
  readonly thumbnail: { readonly avif: string; readonly webp: string };
}

/** Falha ao carregar o módulo se faltar um arquivo: imagem quebrada não chega ao build. */
function fileUrl(id: CastId, width: number, format: "avif" | "webp"): string {
  const name = `oficina-3d-${id}-${String(width)}w.${format}`;
  const url = FILES[`../assets/art/oficina-3d/${name}`];
  if (url === undefined) {
    throw new Error(`Falta a imagem ${name} da cena; rode scripts/arte/gerar-imagens.sh`);
  }
  return url;
}

function srcSetOf(id: CastId, format: "avif" | "webp"): string {
  return WIDTHS.map((width) => `${fileUrl(id, width, format)} ${String(width)}w`).join(", ");
}

function memberOf(id: CastId): CastMember {
  return {
    id,
    ...TEXT.art.goblins[id],
    avif: srcSetOf(id, "avif"),
    webp: srcSetOf(id, "webp"),
    fallback: fileUrl(id, FALLBACK_WIDTH, "webp"),
    thumbnail: {
      avif: fileUrl(id, THUMBNAIL_WIDTH, "avif"),
      webp: fileUrl(id, THUMBNAIL_WIDTH, "webp"),
    },
  };
}

export const CAST: readonly CastMember[] = CAST_IDS.map(memberOf);

export function castMember(id: CastId): CastMember {
  const member = CAST.find((candidate) => candidate.id === id);
  if (member === undefined) {
    throw new Error(`Goblin fora do elenco: ${id}`);
  }
  return member;
}
