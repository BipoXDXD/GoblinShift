import { useEffect, useId, useState, type ReactElement } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { SHARE_ID_PARAM, workbenchPath, workbenchSharedPath } from "../app/paths";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { RecipeCard } from "../recipes/RecipeCard";
import { RECIPE_LIST, recipeOf, recipesAccepting } from "../recipes/registry";
import type { RecipeId } from "../recipes/types";
import { TEXT } from "../text";
import { ShareNotice } from "./ShareNotice";
import { peekShared } from "./sharedInbox";

const T = TEXT.share;

type Shared =
  | { readonly kind: "loading" }
  | { readonly kind: "expired" }
  | { readonly kind: "ready"; readonly names: readonly string[] };

/**
 * Para onde o "Compartilhar" do celular leva (ADR 064): o service worker guardou os arquivos e
 * redirecionou para cá com o id. Pelos nomes, vê quais receitas aceitam todos os arquivos. Se uma
 * receita específica serve, abre a Bancada dela; senão (nenhuma ou mais de uma), mostra a escolha,
 * em que o Downloads Detox, que aceita qualquer arquivo, sempre aparece. A caixa de entrada só é
 * lida de verdade na Bancada, depois do login.
 */
export function SharePage(): ReactElement {
  const [search] = useSearchParams();
  const id = search.get(SHARE_ID_PARAM);
  const navigate = useNavigate();
  const [shared, setShared] = useState<Shared>({ kind: "loading" });

  useEffect(() => {
    if (id === null) {
      return;
    }
    let active = true;
    void peekShared(id)
      .catch(() => "expired" as const)
      .then((names) => {
        if (!active) {
          return;
        }
        if (names === "expired") {
          setShared({ kind: "expired" });
          return;
        }
        const specific = recipesAccepting(names).filter(
          (recipe) => recipeOf(recipe).accept !== null,
        );
        const [only] = specific;
        if (only !== undefined && specific.length === 1) {
          void navigate(workbenchSharedPath(only, id), { replace: true });
          return;
        }
        setShared({ kind: "ready", names });
      });
    return () => {
      active = false;
    };
  }, [id, navigate]);

  return (
    <div className="flex flex-col gap-8">
      <FocusHeading>{T.title}</FocusHeading>
      {/* Sem id não há o que ler: a página já abre como vencida. */}
      <ShareBody shared={id === null ? { kind: "expired" } : shared} id={id} />
    </div>
  );
}

function ShareBody({
  shared,
  id,
}: {
  readonly shared: Shared;
  readonly id: string | null;
}): ReactElement {
  switch (shared.kind) {
    case "loading":
      return <p className="text-fg-muted">{T.loading}</p>;
    case "expired":
      return (
        <>
          <ShareNotice>{T.expired}</ShareNotice>
          <RecipeChoices recipes={RECIPE_LIST.map((recipe) => recipe.id)} shareId={null} />
        </>
      );
    case "ready":
      return (
        <>
          <div className="flex max-w-prose flex-col gap-2">
            <p>{T.received(shared.names)}</p>
            <p className="text-fg-muted">{T.chooseRecipe}</p>
          </div>
          <RecipeChoices recipes={recipesAccepting(shared.names)} shareId={id} />
        </>
      );
  }
}

/** Cartões das receitas, como na Oficina; com `shareId`, a Bancada abre com os arquivos. */
function RecipeChoices({
  recipes,
  shareId,
}: {
  readonly recipes: readonly RecipeId[];
  readonly shareId: string | null;
}): ReactElement {
  const labelId = useId();
  return (
    <section aria-labelledby={labelId} className="flex flex-col gap-4">
      <h2 id={labelId} className="text-sm font-semibold text-fg-muted">
        {T.recipesLabel}
      </h2>
      <ul aria-labelledby={labelId} className="grid gap-4 md:grid-cols-3 md:gap-6">
        {recipes.map((recipeId) => (
          <RecipeCard
            key={recipeId}
            recipeId={recipeId}
            to={shareId === null ? workbenchPath(recipeId) : workbenchSharedPath(recipeId, shareId)}
          />
        ))}
      </ul>
    </section>
  );
}
