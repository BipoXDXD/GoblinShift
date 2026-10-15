import type { ReactElement } from "react";

import adaAvif128 from "../assets/art/goblins/ada-128w.avif";
import adaWebp128 from "../assets/art/goblins/ada-128w.webp";
import adaAvif256 from "../assets/art/goblins/ada-256w.avif";
import adaWebp256 from "../assets/art/goblins/ada-256w.webp";
import ferrugemAvif128 from "../assets/art/goblins/ferrugem-128w.avif";
import ferrugemWebp128 from "../assets/art/goblins/ferrugem-128w.webp";
import ferrugemAvif256 from "../assets/art/goblins/ferrugem-256w.avif";
import ferrugemWebp256 from "../assets/art/goblins/ferrugem-256w.webp";
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
};

/**
 * Uma goblin fixa por estado, para a pessoa associar a personagem à situação (ADR 030):
 * a Lua, calma, espera no vazio; a Ada, inventora, trabalha enquanto carrega; a Nyx, sombria,
 * acompanha o erro; a Ferrugem, a mais expansiva, comemora o sucesso.
 */
const GOBLIN_OF_STATE: Readonly<Record<GoblinState, GoblinId>> = {
  vazio: "lua",
  carregando: "ada",
  erro: "nyx",
  sucesso: "ferrugem",
};

export function goblinOf(state: GoblinState): Goblin {
  return GOBLINS[GOBLIN_OF_STATE[state]];
}

/** Largura e altura exibidas, em px CSS (caixa 4:5; os arquivos têm 1× e 2× disso). */
const WIDTH_PX = 128;
const HEIGHT_PX = 160;

/**
 * A goblin de um estado. Reforça o texto e nunca o substitui: o estado continua dito em texto e
 * na região `aria-live` de quem a usa.
 */
export function GoblinIllustration({ state }: { readonly state: GoblinState }): ReactElement {
  const goblin = goblinOf(state);
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
