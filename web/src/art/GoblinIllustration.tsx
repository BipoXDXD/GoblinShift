import type { ReactElement } from "react";

import adaAvif128 from "../assets/art/goblins/ada-128w.avif";
import adaWebp128 from "../assets/art/goblins/ada-128w.webp";
import adaAvif256 from "../assets/art/goblins/ada-256w.avif";
import adaWebp256 from "../assets/art/goblins/ada-256w.webp";
import fagulhaAvif128 from "../assets/art/goblins/fagulha-128w.avif";
import fagulhaWebp128 from "../assets/art/goblins/fagulha-128w.webp";
import fagulhaAvif256 from "../assets/art/goblins/fagulha-256w.avif";
import fagulhaWebp256 from "../assets/art/goblins/fagulha-256w.webp";
import ferrugemAvif128 from "../assets/art/goblins/ferrugem-128w.avif";
import ferrugemWebp128 from "../assets/art/goblins/ferrugem-128w.webp";
import ferrugemAvif256 from "../assets/art/goblins/ferrugem-256w.avif";
import ferrugemWebp256 from "../assets/art/goblins/ferrugem-256w.webp";
import jadeAvif128 from "../assets/art/goblins/jade-128w.avif";
import jadeWebp128 from "../assets/art/goblins/jade-128w.webp";
import jadeAvif256 from "../assets/art/goblins/jade-256w.avif";
import jadeWebp256 from "../assets/art/goblins/jade-256w.webp";
import luaAvif128 from "../assets/art/goblins/lua-128w.avif";
import luaWebp128 from "../assets/art/goblins/lua-128w.webp";
import luaAvif256 from "../assets/art/goblins/lua-256w.avif";
import luaWebp256 from "../assets/art/goblins/lua-256w.webp";
import nyxAvif128 from "../assets/art/goblins/nyx-128w.avif";
import nyxWebp128 from "../assets/art/goblins/nyx-128w.webp";
import nyxAvif256 from "../assets/art/goblins/nyx-256w.avif";
import nyxWebp256 from "../assets/art/goblins/nyx-256w.webp";
import { TEXT } from "../text";

/** Estados da interface que ganham goblin (decisão D6: goblins só nos estados). */
export const GOBLIN_STATES = ["vazio", "carregando", "erro", "sucesso"] as const;
export type GoblinState = (typeof GOBLIN_STATES)[number];

type GoblinId = keyof typeof TEXT.art.goblins;

interface Goblin {
  readonly name: string;
  readonly alt: string;
  readonly avif: { readonly x1: string; readonly x2: string };
  readonly webp: { readonly x1: string; readonly x2: string };
}

const GOBLINS: Readonly<Record<GoblinId, Goblin>> = {
  lua: {
    ...TEXT.art.goblins.lua,
    avif: { x1: luaAvif128, x2: luaAvif256 },
    webp: { x1: luaWebp128, x2: luaWebp256 },
  },
  ada: {
    ...TEXT.art.goblins.ada,
    avif: { x1: adaAvif128, x2: adaAvif256 },
    webp: { x1: adaWebp128, x2: adaWebp256 },
  },
  nyx: {
    ...TEXT.art.goblins.nyx,
    avif: { x1: nyxAvif128, x2: nyxAvif256 },
    webp: { x1: nyxWebp128, x2: nyxWebp256 },
  },
  ferrugem: {
    ...TEXT.art.goblins.ferrugem,
    avif: { x1: ferrugemAvif128, x2: ferrugemAvif256 },
    webp: { x1: ferrugemWebp128, x2: ferrugemWebp256 },
  },
  jade: {
    ...TEXT.art.goblins.jade,
    avif: { x1: jadeAvif128, x2: jadeAvif256 },
    webp: { x1: jadeWebp128, x2: jadeWebp256 },
  },
  fagulha: {
    ...TEXT.art.goblins.fagulha,
    avif: { x1: fagulhaAvif128, x2: fagulhaAvif256 },
    webp: { x1: fagulhaWebp128, x2: fagulhaWebp256 },
  },
};

/**
 * As goblins de cada estado (ADR 030): a Lua e a Jade, tranquilas, esperam no vazio; a Ada e a
 * Fagulha, de ferramenta e couro de oficina, trabalham enquanto carrega; a Nyx, sombria,
 * acompanha o erro; a Ferrugem, a mais expansiva, comemora o sucesso.
 */
const GOBLINS_OF_STATE: Readonly<Record<GoblinState, readonly [GoblinId, ...GoblinId[]]>> = {
  vazio: ["lua", "jade"],
  carregando: ["ada", "fagulha"],
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

/** Largura e altura exibidas, em px CSS (caixa 4:5; os arquivos têm 1× e 2× disso). */
const WIDTH_PX = 128;
const HEIGHT_PX = 160;

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
    <picture className="shrink-0 self-center md:self-start">
      <source type="image/avif" srcSet={`${goblin.avif.x1} 1x, ${goblin.avif.x2} 2x`} />
      <source type="image/webp" srcSet={`${goblin.webp.x1} 1x, ${goblin.webp.x2} 2x`} />
      <img
        src={goblin.webp.x1}
        alt={goblin.alt}
        width={WIDTH_PX}
        height={HEIGHT_PX}
        loading="lazy"
        decoding="async"
        className="h-40 w-32 object-contain"
      />
    </picture>
  );
}
