import type { RouteObject } from "react-router";

import type { SiteNotice } from "../notice/notice";
import type { UploadServices } from "../upload/useUploadBatch";
import { Layout } from "./Layout";
import { NotFoundPage } from "./NotFoundPage";
import { PATHS } from "./paths";

export interface AppServices {
  readonly uploadServices: UploadServices;
  /** Relógio em ms (Date.now no navegador): o período do Histórico e a validade das chaves. */
  readonly now: () => number;
  /** Aviso global deste build (ADR 031), ou `null`. */
  readonly notice: SiteNotice | null;
}

/**
 * Cada tela é um chunk próprio (`lazy`), baixado só quando a rota abre: o JS inicial fica com o
 * React, o router e a moldura. A 404 fica no chunk de entrada porque a Bancada também a usa e
 * ela é pequena. Os textos legais ficam cada um no chunk da sua rota.
 */
export function createRoutes({ uploadServices, now, notice }: AppServices): RouteObject[] {
  return [
    {
      element: <Layout now={now} notice={notice} />,
      // Na primeira carga o router espera o chunk da tela antes de pintar; é um pedido curto, e
      // um indicador ali piscaria. Sem este componente o React Router avisa no console.
      HydrateFallback: EmptyFallback,
      children: [
        {
          path: PATHS.home,
          lazy: async () => {
            const { HomePage } = await import("./HomePage");
            return { Component: HomePage };
          },
        },
        {
          path: PATHS.workbench,
          lazy: async () => {
            const { WorkbenchPage } = await import("../workbench/WorkbenchPage");
            return { element: <WorkbenchPage uploadServices={uploadServices} /> };
          },
        },
        {
          path: PATHS.history,
          lazy: async () => {
            const { HistoryPage } = await import("../history/HistoryPage");
            return { element: <HistoryPage now={now} /> };
          },
        },
        {
          path: PATHS.signIn,
          lazy: async () => {
            const { SignInPage } = await import("../account/SignInPage");
            return { Component: SignInPage };
          },
        },
        {
          path: PATHS.access,
          lazy: async () => {
            const { AccessPage } = await import("../account/AccessPage");
            return { element: <AccessPage now={now} /> };
          },
        },
        {
          path: PATHS.accountDeleted,
          lazy: async () => {
            const { AccountDeletedPage } = await import("../account/AccountDeletedPage");
            return { Component: AccountDeletedPage };
          },
        },
        {
          path: PATHS.acceptTerms,
          lazy: async () => {
            const { AcceptTermsPage } = await import("../terms/AcceptTermsPage");
            return { Component: AcceptTermsPage };
          },
        },
        {
          path: PATHS.terms,
          lazy: async () => {
            const { TermsPage } = await import("../legal/TermsPage");
            return { Component: TermsPage };
          },
        },
        {
          path: PATHS.privacy,
          lazy: async () => {
            const { PrivacyPage } = await import("../legal/PrivacyPage");
            return { Component: PrivacyPage };
          },
        },
        {
          path: PATHS.credits,
          lazy: async () => {
            const { CreditsPage } = await import("../legal/CreditsPage");
            return { Component: CreditsPage };
          },
        },
        {
          path: PATHS.run,
          lazy: async () => {
            const { RunPage } = await import("../runs/RunPage");
            return { Component: RunPage };
          },
        },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ];
}

function EmptyFallback(): null {
  return null;
}
