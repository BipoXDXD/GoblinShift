import { useEffect, useId, useState, type ReactElement } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { RecentRuns } from "../history/RecentRuns";
import { OficinaHero } from "../oficina3d/OficinaHero";
import { PresetsSection } from "../presets/PresetsSection";
import { RecipeCard } from "../recipes/RecipeCard";
import { RECIPE_LIST } from "../recipes/registry";
import { ShareNotice } from "../share/ShareNotice";
import { TEXT } from "../text";
import { buttonClassName } from "../components/ui/Button";
import { SHARE_STATUS_PARAM, SHARE_UNAVAILABLE, workbenchPath, workbenchSamplePath } from "./paths";

const T = TEXT.home;
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
  const shareUnavailable = useShareUnavailable();
  return (
    <div className="flex flex-col gap-12 md:gap-16">
      {/* A partir de 1024 px o palco é alto (480 px mais controles e seletor): o título sobe para o
          topo, ao lado do palco, em vez de boiar no meio da coluna, e a faixa perde 32 px de
          padding vertical, para as receitas ficarem mais perto da primeira tela. */}
      <section className="flex flex-col gap-8 rounded-lg bg-hero-band px-6 py-12 md:flex-row md:items-center md:px-12 md:py-16 lg:items-start lg:py-12">
        <div className="flex max-w-prose flex-col gap-4">
          <FocusHeading size="hero">{T.title}</FocusHeading>
          <p className="text-lg text-fg-muted">{T.lead}</p>
          {shareUnavailable && <ShareNotice role="status">{TEXT.share.unavailable}</ShareNotice>}
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
            <RecipeCard key={recipe.id} recipeId={recipe.id} to={workbenchPath(recipe.id)} />
          ))}
        </ul>
      </section>
      <PresetsSection />
      <RecentRuns />
    </div>
  );
}

/**
 * O "Compartilhar" do celular sem service worker cai em `/?compartilhamento=indisponivel` (Nginx,
 * ADR 064). O aviso fica até sair da Oficina, e o parâmetro sai da URL: recarregar ou voltar não o
 * repete. Ele vem depois do H1, que recebe o foco, como `status`: não interrompe a leitura.
 */
function useShareUnavailable(): boolean {
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const [unavailable] = useState(() => search.get(SHARE_STATUS_PARAM) === SHARE_UNAVAILABLE);
  const inUrl = search.has(SHARE_STATUS_PARAM);
  useEffect(() => {
    if (inUrl) {
      void navigate({ search: "" }, { replace: true });
    }
  }, [inUrl, navigate]);
  return unavailable;
}
