import type { ReactElement } from "react";
import { Link } from "react-router";

import emblemAvif48 from "../assets/art/marca/rubia-escudo-48h.avif";
import emblemWebp48 from "../assets/art/marca/rubia-escudo-48h.webp";
import emblemAvif96 from "../assets/art/marca/rubia-escudo-96h.avif";
import emblemWebp96 from "../assets/art/marca/rubia-escudo-96h.webp";
import emblemAvif144 from "../assets/art/marca/rubia-escudo-144h.avif";
import emblemWebp144 from "../assets/art/marca/rubia-escudo-144h.webp";
import { TEXT } from "../text";
import { PATHS } from "./paths";

const T = TEXT.layout;

const AVIF_SRCSET = `${emblemAvif48} 1x, ${emblemAvif96} 2x, ${emblemAvif144} 3x`;
const WEBP_SRCSET = `${emblemWebp48} 1x, ${emblemWebp96} 2x, ${emblemWebp144} 3x`;
/**
 * Caixa do emblema em px CSS, a do arquivo 1×: fixa no HTML, para o cabeçalho não pular quando a
 * imagem chega. 48 px de altura é o passo da escala de espaço mais perto dos ~40 px pedidos.
 */
const EMBLEM_WIDTH_PX = 45;
const EMBLEM_HEIGHT_PX = 48;

/**
 * Lockup da marca (decisão do usuário em 2026-10-15): o emblema a1, decorativo, e o nome em texto,
 * que é o nome acessível do link da Oficina (sem complemento escondido: o Chromium separaria com
 * espaço o texto de um span sr-only, e o nome visível já basta, WCAG 2.5.3). Cabe inteiro em 360 px, então não há versão só com o
 * emblema.
 */
export function BrandLink(): ReactElement {
  return (
    <Link
      to={PATHS.home}
      className="inline-flex min-h-11 items-center gap-2 font-display text-xl font-semibold text-fg no-underline"
    >
      <picture className="flex-none">
        <source type="image/avif" srcSet={AVIF_SRCSET} />
        <source type="image/webp" srcSet={WEBP_SRCSET} />
        <img
          src={emblemWebp48}
          alt=""
          width={EMBLEM_WIDTH_PX}
          height={EMBLEM_HEIGHT_PX}
          decoding="async"
          className="block h-12 w-auto"
        />
      </picture>
      <span>{T.brand}</span>
    </Link>
  );
}
