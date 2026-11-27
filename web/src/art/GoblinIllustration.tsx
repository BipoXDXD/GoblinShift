import { useId, type ReactElement } from "react";

import { TEXT } from "../text";

/** Estados da interface que ganham goblin (decisão D6: goblins só nos estados). */
export const GOBLIN_STATES = ["vazio", "carregando", "erro", "sucesso"] as const;
export type GoblinState = (typeof GOBLIN_STATES)[number];

type GoblinId = keyof typeof TEXT.art.goblins;

/**
 * Larguras geradas por scripts/arte/gerar-imagens.sh: cobrem a caixa do estado (256 a 384 px CSS)
 * e a grade dos créditos, de 1× a 3×.
 */
const WIDTHS = [256, 384, 512, 768, 1024, 1280] as const;
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
  lua: goblinFrom("lua"),
  nyx: goblinFrom("nyx"),
};

/** As seis goblins publicadas, para quem as apresenta todas (créditos). */
export const CAST: readonly Goblin[] = Object.values(GOBLINS);

/**
 * As goblins de cada estado (ADR 030): a Lua e a Jade, tranquilas, esperam no vazio; a Rúbia,
 * mascote da oficina, e a Fagulha, de couro e luvas de trabalho, trabalham enquanto carrega; a
 * Nyx, sombria, acompanha o erro; a Ferrugem, a mais expansiva, comemora o sucesso. A Ada saiu do
 * site em 2026-10-15, por decisão do usuário.
 */
const GOBLINS_OF_STATE: Readonly<Record<GoblinState, readonly [GoblinId, ...GoblinId[]]>> = {
  vazio: ["lua", "jade"],
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
 * Caixa do estado em px CSS (4:5, como os arquivos): 256×320 no celular e 320×400 a partir de
 * 768 px. Não cresce mais no desktop: com 480 px a goblin empurrava o Baixar e a ação do vazio
 * para fora da primeira tela de 1280×800 e virava o assunto do cartão. Com width e height no
 * `<img>`, o navegador guarda o lugar antes de baixar (sem CLS); o `sizes` acompanha as classes,
 * para cada tela baixar só a largura que cobre a caixa.
 */
const STATE_WIDTH_PX = 320;
const STATE_HEIGHT_PX = 400;
const STATE_SIZES = "(min-width: 768px) 320px, 256px";

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
      width={STATE_WIDTH_PX}
      height={STATE_HEIGHT_PX}
      pictureClassName="shrink-0 self-center"
      imgClassName="h-80 w-64 md:h-100 md:w-80"
    />
  );
}

/** Na grade dos créditos: duas colunas no celular, três a partir de 768 px. */
const CAST_SIZES = "(min-width: 768px) 224px, 45vw";
/** A proporção reservada na grade (4:5); a largura exibida vem da coluna. */
const CAST_WIDTH_PX = 256;
const CAST_HEIGHT_PX = 320;

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
                width={CAST_WIDTH_PX}
                height={CAST_HEIGHT_PX}
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
  /** Caixa reservada em px CSS, antes de a imagem chegar (sem CLS). */
  readonly width: number;
  readonly height: number;
  /** Posição no layout de quem usa. */
  readonly pictureClassName: string;
  /** Tamanho exibido; a proporção já vem reservada por width e height. */
  readonly imgClassName: string;
}

/**
 * Toda goblin desenhada passa por aqui, com `goblin-silhouette`: no tema escuro o token põe um fio
 * de luz e um halo atrás da figura, para roupa e cabelo pretos não sumirem no cartão (tokens.css).
 */
function GoblinPicture({
  goblin,
  alt,
  sizes,
  width,
  height,
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
        width={width}
        height={height}
        loading="lazy"
        decoding="async"
        className={`goblin-silhouette object-contain ${imgClassName}`}
      />
    </picture>
  );
}
