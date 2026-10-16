import { useId, type ReactElement } from "react";

import { TEXT } from "../text";

/** Estados da interface que ganham goblin (decisão D6: goblins só nos estados). */
export const GOBLIN_STATES = ["vazio", "carregando", "erro", "sucesso"] as const;
export type GoblinState = (typeof GOBLIN_STATES)[number];

type GoblinId = keyof typeof TEXT.art.goblins;

/** Larguras geradas por scripts/arte/gerar-imagens.sh: a caixa de 192 a 256 px CSS, de 1× a 3×. */
const WIDTHS = [256, 384, 512, 768] as const;
const FALLBACK_WIDTH = 384;

const FILES = import.meta.glob<string>("../assets/art/goblins/*.{avif,webp}", {
  eager: true,
  import: "default",
});

interface Goblin {
  readonly name: string;
  readonly alt: string;
  /** `srcset` com descritor de largura, um por formato. */
  readonly avif: string;
  readonly webp: string;
  /** WebP de 384 px, para navegador sem `<picture>`. */
  readonly fallback: string;
}

/** Falha ao carregar o módulo se faltar um arquivo: imagem quebrada não chega ao build. */
function fileUrl(id: GoblinId, width: number, format: "avif" | "webp"): string {
  const name = `${id}-${String(width)}w.${format}`;
  const url = FILES[`../assets/art/goblins/${name}`];
  if (url === undefined) {
    throw new Error(`Falta a imagem ${name} da goblin; rode scripts/arte/gerar-imagens.sh`);
  }
  return url;
}

function srcSetOf(id: GoblinId, format: "avif" | "webp"): string {
  return WIDTHS.map((width) => `${fileUrl(id, width, format)} ${String(width)}w`).join(", ");
}

function goblinFrom(id: GoblinId): Goblin {
  return {
    ...TEXT.art.goblins[id],
    avif: srcSetOf(id, "avif"),
    webp: srcSetOf(id, "webp"),
    fallback: fileUrl(id, FALLBACK_WIDTH, "webp"),
  };
}

/** A ordem é a do elenco na página de créditos: a Rúbia, mascote da abertura, primeiro. */
const GOBLINS: Readonly<Record<GoblinId, Goblin>> = {
  rubia: goblinFrom("rubia"),
  fagulha: goblinFrom("fagulha"),
  ferrugem: goblinFrom("ferrugem"),
  jade: goblinFrom("jade"),
  nyx: goblinFrom("nyx"),
};

/** As cinco goblins publicadas, para quem as apresenta todas (créditos). */
export const CAST: readonly Goblin[] = Object.values(GOBLINS);

/**
 * As goblins de cada estado (ADR 030): a Jade, tranquila, espera no vazio; a Rúbia,
 * mascote da oficina, e a Fagulha, de couro e luvas de trabalho, trabalham enquanto carrega; a
 * Nyx, sombria, acompanha o erro; a Ferrugem, a mais expansiva, comemora o sucesso. A Ada saiu do
 * site em 2026-10-15 e a Lua em 2026-10-16, por decisão do usuário.
 */
const GOBLINS_OF_STATE: Readonly<Record<GoblinState, readonly [GoblinId, ...GoblinId[]]>> = {
  vazio: ["jade"],
  carregando: ["rubia", "fagulha"],
  erro: ["nyx"],
  sucesso: ["ferrugem"],
};

export function goblinsOf(state: GoblinState): readonly Goblin[] {
  return GOBLINS_OF_STATE[state].map((id) => GOBLINS[id]);
}

/**
 * Rotação estável: a mesma chave (o id da execução, a receita, a rota) dá sempre a mesma goblin,
 * então ela não troca a cada render nem a cada polling, e chaves diferentes se espalham entre as
 * goblins do estado.
 */
export function goblinOf(state: GoblinState, rotationKey: string): Goblin {
  const ids = GOBLINS_OF_STATE[state];
  const id = ids[stableHash(rotationKey) % ids.length] ?? ids[0];
  return GOBLINS[id];
}

const FNV_OFFSET_BASIS = 0x811c9dc5;
const FNV_PRIME = 0x01000193;

/** FNV-1a de 32 bits: barato, determinístico e bem espalhado para chaves curtas. */
function stableHash(key: string): number {
  let hash = FNV_OFFSET_BASIS;
  for (let index = 0; index < key.length; index += 1) {
    hash ^= key.charCodeAt(index);
    hash = Math.imul(hash, FNV_PRIME) >>> 0;
  }
  return hash;
}

/**
 * Caixa reservada em px CSS (4:5, como os arquivos): 192×240 no celular e 256×320 a partir de
 * 768 px. Com width e height no `<img>`, o navegador guarda o lugar antes de baixar (sem CLS).
 */
const WIDTH_PX = 256;
const HEIGHT_PX = 320;
const STATE_SIZES = "(min-width: 768px) 256px, 192px";

/**
 * A goblin de um estado. Reforça o texto e nunca o substitui: o estado continua dito em texto e
 * na região `aria-live` de quem a usa.
 */
interface GoblinIllustrationProps {
  readonly state: GoblinState;
  /** O que identifica a situação (id da execução, receita, rota): escolhe a goblin do estado. */
  readonly rotationKey: string;
}

export function GoblinIllustration({ state, rotationKey }: GoblinIllustrationProps): ReactElement {
  const goblin = goblinOf(state, rotationKey);
  return (
    <GoblinPicture
      goblin={goblin}
      alt={goblin.alt}
      sizes={STATE_SIZES}
      pictureClassName="shrink-0 self-center"
      imgClassName="h-60 w-48 md:h-80 md:w-64"
    />
  );
}

/** Na grade dos créditos: duas colunas no celular, três a partir de 768 px. */
const CAST_SIZES = "(min-width: 768px) 224px, 45vw";

/** O elenco inteiro, cada goblin com o nome na legenda (página de créditos). */
export function GoblinCast(): ReactElement {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-4">
      <h2 id={headingId} className="font-display text-xl font-semibold text-fg">
        {TEXT.art.castHeading}
      </h2>
      <ul aria-labelledby={headingId} className="grid grid-cols-2 gap-4 md:grid-cols-3 md:gap-6">
        {CAST.map((goblin) => (
          <li key={goblin.name}>
            <figure className="flex flex-col items-center gap-2 rounded-lg bg-surface p-4 shadow-card">
              {/* A legenda já diz o nome: a foto é decorativa. */}
              <GoblinPicture
                goblin={goblin}
                alt=""
                sizes={CAST_SIZES}
                pictureClassName="w-full"
                imgClassName="h-auto w-full"
              />
              <figcaption className="font-display text-lg font-semibold text-fg">
                {goblin.name}
              </figcaption>
            </figure>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface GoblinPictureProps {
  readonly goblin: Goblin;
  readonly alt: string;
  readonly sizes: string;
  /** Posição no layout de quem usa. */
  readonly pictureClassName: string;
  /** Tamanho exibido; a proporção já vem reservada por width e height. */
  readonly imgClassName: string;
}

function GoblinPicture({
  goblin,
  alt,
  sizes,
  pictureClassName,
  imgClassName,
}: GoblinPictureProps): ReactElement {
  return (
    <picture className={pictureClassName}>
      <source type="image/avif" srcSet={goblin.avif} sizes={sizes} />
      <source type="image/webp" srcSet={goblin.webp} sizes={sizes} />
      <img
        src={goblin.fallback}
        alt={alt}
        width={WIDTH_PX}
        height={HEIGHT_PX}
        loading="lazy"
        decoding="async"
        className={`object-contain ${imgClassName}`}
      />
    </picture>
  );
}
