import { ChevronRight } from "lucide-react";
import type { ReactElement } from "react";
import { Link } from "react-router";

import { TEXT } from "../text";
import { recipeOf } from "./registry";
import type { RecipeId } from "./types";

const T = TEXT.home;
const SUMMARIES: Readonly<Record<RecipeId, string>> = T.summaries;

/** Cartão de uma receita que abre a Bancada em `to` (Oficina e página do compartilhamento). */
export function RecipeCard({
  recipeId,
  to,
}: {
  readonly recipeId: RecipeId;
  readonly to: string;
}): ReactElement {
  const recipe = recipeOf(recipeId);
  return (
    <li className="relative flex flex-col gap-3 rounded-lg bg-surface p-6 shadow-card transition hover:shadow-float">
      <h3 className="font-display text-xl font-semibold text-fg">{recipe.title}</h3>
      <p className="flex-1 text-fg-muted">{SUMMARIES[recipeId]}</p>
      {/* O link cobre o cartão inteiro (card-link, base.css); o foco aparece no cartão. */}
      <Link to={to} className="card-link inline-flex min-h-11 items-center gap-1 font-semibold">
        {T.openWorkbench}
        {/* O espaço fica fora do sr-only: fora do fluxo, o span perde o espaço inicial,
            e o nome acessível juntava as palavras ("Bancadada"). */}{" "}
        <span className="sr-only">{T.openWorkbenchOf(recipe.title)}</span>
        <ChevronRight aria-hidden="true" className="size-4" />
      </Link>
    </li>
  );
}
