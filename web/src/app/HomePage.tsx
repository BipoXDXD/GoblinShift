import { ChevronRight } from "lucide-react";
import { useId, type ReactElement } from "react";
import { Link } from "react-router";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { RecentRuns } from "../history/RecentRuns";
import { OficinaHero } from "../oficina3d/OficinaHero";
import { PresetsSection } from "../presets/PresetsSection";
import { RECIPE_LIST } from "../recipes/registry";
import type { RecipeId } from "../recipes/types";
import { TEXT } from "../text";
import { buttonClassName } from "../components/ui/Button";
import { workbenchPath, workbenchSamplePath } from "./paths";

const T = TEXT.home;
const SUMMARIES: Readonly<Record<RecipeId, string>> = T.summaries;
/** Âncora da seção das receitas, alvo do "ou escolha uma receita" da abertura. */
const RECIPES_ANCHOR = "receitas";

/**
 * Oficina (2.2): a abertura e as receitas. A ação primária da abertura é "Testar com um exemplo"
 * (D4), que abre a Bancada do Image Forge já com a imagem de exemplo; os cartões seguem como a
 * escolha de receita, e as execuções recentes vêm depois deles. A abertura tem o elenco em 3D, uma goblin por
 * vez, que chega depois do conteúdo (ADR 027, 030 e 038).
 */
export function HomePage(): ReactElement {
  const recipesHeadingId = useId();
  return (
    <div className="flex flex-col gap-12 md:gap-16">
      {/* A partir de 1024 px o palco é alto (480 px mais controles e seletor): o título sobe para o
          topo, ao lado do palco, em vez de boiar no meio da coluna, e a faixa perde 32 px de
          padding vertical, para as receitas ficarem mais perto da primeira tela. */}
      <section className="flex flex-col gap-8 rounded-lg bg-hero-band px-6 py-12 md:flex-row md:items-center md:px-12 md:py-16 lg:items-start lg:py-12">
        <div className="flex max-w-prose flex-col gap-4">
          <FocusHeading size="hero">{T.title}</FocusHeading>
          <p className="text-lg text-fg-muted">{T.lead}</p>
          {/* Antes da cena na ordem do DOM: no celular, a cena e os cartões ficam abaixo da dobra,
              e o primeiro uso fica a um clique. */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <Link
              to={workbenchSamplePath("image-forge")}
              className={buttonClassName({ variant: "primary", size: "lg" })}
            >
              {T.trySample}
            </Link>
            <a href={`#${RECIPES_ANCHOR}`} className={buttonClassName({ variant: "tertiary" })}>
              {T.orChoose}
            </a>
          </div>
        </div>
        <OficinaHero />
      </section>
      <section
        id={RECIPES_ANCHOR}
        aria-labelledby={recipesHeadingId}
        className="flex scroll-mt-4 flex-col gap-4"
      >
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
