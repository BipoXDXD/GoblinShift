import { ChevronRight } from "lucide-react";
import { useId, type ReactElement } from "react";
import { Link, useSearchParams } from "react-router";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { RecentRuns } from "../history/RecentRuns";
import { OficinaHero } from "../oficina3d/OficinaHero";
import { PresetsSection } from "../presets/PresetsSection";
import { RECIPE_LIST } from "../recipes/registry";
import type { RecipeId } from "../recipes/types";
import { ShareNotice } from "../share/ShareNotice";
import { TEXT } from "../text";
import { SHARE_STATUS_PARAM, SHARE_UNAVAILABLE, workbenchPath } from "./paths";

const T = TEXT.home;
const SUMMARIES: Readonly<Record<RecipeId, string>> = T.summaries;

/**
 * Oficina (2.2): a abertura e as receitas. A ação são os cartões, então a tela não tem botão
 * primário; as execuções recentes vêm depois das receitas. A abertura tem o elenco em 3D, uma goblin por
 * vez, que chega depois do conteúdo (ADR 027, 030 e 038).
 */
export function HomePage(): ReactElement {
  const recipesHeadingId = useId();
  const [search] = useSearchParams();
  // O "Compartilhar" do celular sem service worker cai aqui (Nginx, ADR 064).
  const shareUnavailable = search.get(SHARE_STATUS_PARAM) === SHARE_UNAVAILABLE;
  return (
    <div className="flex flex-col gap-12 md:gap-16">
      {shareUnavailable && <ShareNotice>{TEXT.share.unavailable}</ShareNotice>}
      {/* A partir de 1024 px o palco é alto (480 px mais controles e seletor): o título sobe para o
          topo, ao lado do palco, em vez de boiar no meio da coluna, e a faixa perde 32 px de
          padding vertical, para as receitas ficarem mais perto da primeira tela. */}
      <section className="flex flex-col gap-8 rounded-lg bg-hero-band px-6 py-12 md:flex-row md:items-center md:px-12 md:py-16 lg:items-start lg:py-12">
        <div className="flex max-w-prose flex-col gap-4">
          <FocusHeading size="hero">{T.title}</FocusHeading>
          <p className="text-lg text-fg-muted">{T.lead}</p>
        </div>
        <OficinaHero />
      </section>
      <section aria-labelledby={recipesHeadingId} className="flex flex-col gap-4">
        <h2 id={recipesHeadingId} className="text-sm font-semibold text-fg-muted">
          {T.recipesHeading}
        </h2>
        <ul className="grid gap-4 md:grid-cols-3 md:gap-6">
          {RECIPE_LIST.map((recipe) => (
            <li
              key={recipe.id}
              className="relative flex flex-col gap-3 rounded-lg bg-surface p-6 shadow-card transition hover:shadow-float"
            >
              <h3 className="font-display text-xl font-semibold text-fg">{recipe.title}</h3>
              <p className="flex-1 text-fg-muted">{SUMMARIES[recipe.id]}</p>
              {/* O link cobre o cartão inteiro (card-link, base.css); o foco aparece no cartão. */}
              <Link
                to={workbenchPath(recipe.id)}
                className="card-link inline-flex min-h-11 items-center gap-1 font-semibold"
              >
                {T.openWorkbench}
                {/* O espaço fica fora do sr-only: fora do fluxo, o span perde o espaço inicial,
                    e o nome acessível juntava as palavras ("Bancadada"). */}{" "}
                <span className="sr-only">{T.openWorkbenchOf(recipe.title)}</span>
                <ChevronRight aria-hidden="true" className="size-4" />
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <PresetsSection />
      <RecentRuns />
    </div>
  );
}
