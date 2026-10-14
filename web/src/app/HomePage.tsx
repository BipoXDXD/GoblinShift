import { ChevronRight } from "lucide-react";
import { useId, type ReactElement } from "react";
import { Link } from "react-router";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { RecentRuns } from "../history/RecentRuns";
import { RECIPE_LIST } from "../recipes/registry";
import type { RecipeId } from "../recipes/types";
import { TEXT } from "../text";
import { workbenchPath } from "./paths";

const T = TEXT.home;
const SUMMARIES: Readonly<Record<RecipeId, string>> = T.summaries;

/**
 * Oficina (2.2): a abertura e as receitas. A ação são os cartões, então a tela não tem botão
 * primário; as execuções recentes vêm depois das receitas. A cena da oficina (P11) entra com a
 * arte.
 */
export function HomePage(): ReactElement {
  const recipesHeadingId = useId();
  return (
    <div className="flex flex-col gap-12 md:gap-16">
      <section className="flex flex-col gap-8 rounded-lg bg-hero-band px-6 py-12 md:flex-row md:items-center md:px-12 md:py-16">
        <div className="flex max-w-prose flex-col gap-4">
          <FocusHeading size="hero">{T.title}</FocusHeading>
          <p className="text-lg text-fg-muted">{T.lead}</p>
        </div>
        {/* Ponto de montagem da cena 3D da abertura (ADR 027): fica vazio e fora da página até o
            asset existir; nada essencial pode depender dele. */}
        <div data-oficina-3d="" hidden />
      </section>
      <section aria-labelledby={recipesHeadingId} className="flex flex-col gap-4">
        <h2 id={recipesHeadingId} className="text-sm font-semibold text-fg-muted">
          {T.recipesHeading}
        </h2>
        <ul className="grid gap-4 md:grid-cols-3 md:gap-6">
          {RECIPE_LIST.map((recipe) => (
            <li
              key={recipe.id}
              className="relative flex flex-col gap-3 rounded-lg bg-surface p-6 shadow-2 transition hover:shadow-3"
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
      <RecentRuns />
    </div>
  );
}
