import type { RouteObject } from "react-router";

import type { SiteNotice } from "../notice/notice";
import { RECIPE_IDS, RECIPE_TITLES } from "../recipes/titles";
import type { UploadServices } from "../upload/useUploadBatch";
import { TEXT } from "../text";
import { Layout } from "./Layout";
import { NotFoundPage } from "./NotFoundPage";
import type { PageTitleHandle } from "./pageTitle";
import { PATHS, workbenchPath } from "./paths";
import { RootFailure, RouteFailure } from "./RouteFailure";

export interface AppServices {
  readonly uploadServices: UploadServices;
  /** Relógio em ms (Date.now no navegador): o período do Histórico e a validade das chaves. */
  readonly now: () => number;
  /** Aviso global deste build (ADR 031), ou `null`. */
  readonly notice: SiteNotice | null;
}

/**
 * Cada tela é um chunk próprio (`lazy`), baixado só quando a rota abre: o JS inicial fica com o
 * React, o router e a moldura. A 404 fica no chunk de entrada porque é pequena e o `*` a usa
 * para qualquer URL, inclusive a Bancada de uma receita que não existe. Os textos legais ficam cada um no chunk da sua rota.
 */
export function createRoutes({ uploadServices, now, notice }: AppServices): RouteObject[] {
  return [
    {
      element: <Layout now={now} notice={notice} />,
      errorElement: <RootFailure />,
      // Na primeira carga o router espera o chunk da tela antes de pintar; é um pedido curto, e
      // um indicador ali piscaria. Sem este componente o React Router avisa no console.
      HydrateFallback: EmptyFallback,
      children: withRouteFailure([
        {
          path: PATHS.home,
          lazy: async () => {
            const { HomePage } = await import("./HomePage");
            return { Component: HomePage };
          },
        },
        ...workbenchRoutes(uploadServices),
        {
          path: PATHS.history,
          handle: titled(TEXT.history.title),
          lazy: async () => {
            const { HistoryPage } = await import("../history/HistoryPage");
            return { element: <HistoryPage now={now} /> };
          },
        },
        {
          path: PATHS.signIn,
          handle: titled(TEXT.account.signIn.navSignIn),
          lazy: async () => {
            const { SignInPage } = await import("../account/SignInPage");
            return { Component: SignInPage };
          },
        },
        {
          path: PATHS.access,
          handle: titled(TEXT.account.access.title),
          lazy: async () => {
            const { AccessPage } = await import("../account/AccessPage");
            return { element: <AccessPage now={now} /> };
          },
        },
        {
          path: PATHS.apiGuide,
          handle: titled(TEXT.apiGuide.title),
          lazy: async () => {
            const { ApiGuidePage } = await import("../account/ApiGuidePage");
            return { Component: ApiGuidePage };
          },
        },
        {
          path: PATHS.accountDeleted,
          handle: titled(TEXT.account.deletion.doneTitle),
          lazy: async () => {
            const { AccountDeletedPage } = await import("../account/AccountDeletedPage");
            return { Component: AccountDeletedPage };
          },
        },
        {
          path: PATHS.acceptTerms,
          handle: titled(TEXT.pageTitles.acceptTerms),
          lazy: async () => {
            const { AcceptTermsPage } = await import("../terms/AcceptTermsPage");
            return { Component: AcceptTermsPage };
          },
        },
        {
          path: PATHS.terms,
          handle: titled(TEXT.legal.termsTitle),
          lazy: async () => {
            const { TermsPage } = await import("../legal/TermsPage");
            return { Component: TermsPage };
          },
        },
        {
          path: PATHS.privacy,
          handle: titled(TEXT.legal.privacyTitle),
          lazy: async () => {
            const { PrivacyPage } = await import("../legal/PrivacyPage");
            return { Component: PrivacyPage };
          },
        },
        {
          path: PATHS.credits,
          handle: titled(TEXT.credits.title),
          lazy: async () => {
            const { CreditsPage } = await import("../legal/CreditsPage");
            return { Component: CreditsPage };
          },
        },
        {
          path: PATHS.run,
          handle: titled(TEXT.run.title),
          lazy: async () => {
            const { RunPage } = await import("../runs/RunPage");
            return { Component: RunPage };
          },
        },
        { path: "*", element: <NotFoundPage />, handle: titled(TEXT.notFound.title) },
      ]),
    },
  ];
}

/**
 * Falha ao abrir uma tela, dentro da moldura. Sem isso, um chunk que não vem (aba aberta durante
 * uma publicação) cai na tela padrão do React Router, em inglês e sem navegação.
 */
function withRouteFailure(screens: RouteObject[]): RouteObject[] {
  return [{ errorElement: <RouteFailure />, children: screens }];
}

/**
 * Uma rota da Bancada por receita do registro, para a aba dizer qual receita está aberta (WCAG
 * 2.4.2). O id que não existe cai no `*`, com o título da página não encontrada.
 * `caseSensitive` mantém a regra do registro: `CSV-Janitor` não é receita.
 */
function workbenchRoutes(uploadServices: UploadServices): RouteObject[] {
  return [
    ...RECIPE_IDS.map((recipeId) => ({
      path: workbenchPath(recipeId),
      caseSensitive: true,
      handle: titled(TEXT.workbench.title(RECIPE_TITLES[recipeId])),
      lazy: async () => {
        const { WorkbenchPage } = await import("../workbench/WorkbenchPage");
        return { element: <WorkbenchPage recipeId={recipeId} uploadServices={uploadServices} /> };
      },
    })),
  ];
}

/** Título da aba da rota (Layout); a Oficina fica só com o nome do site. */
function titled(title: string): PageTitleHandle {
  return { title };
}

function EmptyFallback(): null {
  return null;
}
