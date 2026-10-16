import type { ReactElement } from "react";

import { GoblinCast } from "../art/GoblinIllustration";
import sealAvif128 from "../assets/art/marca/selo-oficina-128h.avif";
import sealWebp128 from "../assets/art/marca/selo-oficina-128h.webp";
import sealAvif256 from "../assets/art/marca/selo-oficina-256h.avif";
import sealWebp256 from "../assets/art/marca/selo-oficina-256h.webp";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";

const T = TEXT.credits;
/** O selo b1, a marca secundária: só aqui no site (e na imagem de compartilhamento). */
const SEAL_PX = 128;
const BRAND_HEADING_ID = "creditos-marca";

/**
 * /creditos: de onde vieram as ilustrações e as fontes. Curto e fora dos textos jurídicos de
 * docs/legal/, que são versionados e aceitos (ADR 029); o registro completo está em
 * docs/art/LICENSE-ART.md.
 */
export function CreditsPage(): ReactElement {
  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <FocusHeading>{T.title}</FocusHeading>
      <section className="flex max-w-prose flex-col gap-3">
        <h2 className="font-display text-xl font-semibold text-fg">{T.artHeading}</h2>
        {T.art.map((paragraph) => (
          <p key={paragraph}>{paragraph}</p>
        ))}
      </section>
      <GoblinCast />
      <section
        aria-labelledby={BRAND_HEADING_ID}
        className="flex max-w-prose flex-col gap-4 md:flex-row md:items-start md:gap-6"
      >
        <picture className="flex-none self-center md:self-start">
          <source type="image/avif" srcSet={`${sealAvif128} 1x, ${sealAvif256} 2x`} />
          <source type="image/webp" srcSet={`${sealWebp128} 1x, ${sealWebp256} 2x`} />
          <img
            src={sealWebp128}
            alt={T.sealAlt}
            width={SEAL_PX}
            height={SEAL_PX}
            loading="lazy"
            decoding="async"
            className="block size-32"
          />
        </picture>
        <div className="flex flex-col gap-3">
          <h2 id={BRAND_HEADING_ID} className="font-display text-xl font-semibold text-fg">
            {T.brandHeading}
          </h2>
          {T.brand.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </div>
      </section>
      <section className="flex max-w-prose flex-col gap-3">
        <h2 className="font-display text-xl font-semibold text-fg">{T.fontsHeading}</h2>
        <p>{T.fonts}</p>
      </section>
    </article>
  );
}
