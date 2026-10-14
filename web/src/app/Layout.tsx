import type { ReactElement } from "react";
import { Link, NavLink, Outlet } from "react-router";

import { TEXT } from "../text";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import { PATHS } from "./paths";

const T = TEXT.layout;
/** Alvo do "Pular para o conteúdo"; o `<main>` é o mesmo em todas as telas. */
const MAIN_ID = "conteudo";

/**
 * Moldura de todas as telas (D8): faixa de acento em latão, barra no topo com a marca, o Histórico
 * e o seletor de tema, e o conteúdo centrado. Acesso entra na navegação com a fatia 6, para a
 * barra não prometer o que ainda não há (RUI cap. 1, "Be a pessimist").
 */
export function Layout(): ReactElement {
  return (
    <div className="flex min-h-screen flex-col">
      <a
        href={`#${MAIN_ID}`}
        className="sr-only rounded-lg bg-surface-raised px-4 py-2 font-semibold shadow-3 focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-20"
      >
        {T.skipToContent}
      </a>
      <div aria-hidden="true" className="h-1 bg-accent-band" />
      <header className="border-b border-line-soft bg-surface">
        <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-4 md:px-8">
          <Link
            to={PATHS.home}
            aria-label={T.brandLink}
            className="inline-flex min-h-11 items-center font-display text-xl font-semibold text-fg no-underline"
          >
            {T.brand}
          </Link>
          <div className="flex items-center gap-4">
            <nav aria-label={T.navLabel}>
              <NavLink
                to={PATHS.history}
                className="inline-flex min-h-11 items-center font-semibold aria-[current=page]:text-fg aria-[current=page]:no-underline"
              >
                {TEXT.history.navLink}
              </NavLink>
            </nav>
            <ThemeSwitcher />
          </div>
        </div>
      </header>
      <main id={MAIN_ID} className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 md:px-8 md:py-12">
        <Outlet />
      </main>
    </div>
  );
}
