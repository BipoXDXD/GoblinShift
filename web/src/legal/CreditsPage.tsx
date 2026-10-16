import type { ReactElement } from "react";

import { GoblinCast } from "../art/GoblinIllustration";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";

const T = TEXT.credits;

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
      <section className="flex max-w-prose flex-col gap-3">
        <h2 className="font-display text-xl font-semibold text-fg">{T.fontsHeading}</h2>
        <p>{T.fonts}</p>
      </section>
    </article>
  );
}
