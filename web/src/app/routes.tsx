import type { RouteObject } from "react-router";

import type { UploadServices } from "../upload/useUploadBatch";
import { Layout } from "./Layout";
import { NotFoundPage } from "./NotFoundPage";
import { PATHS } from "./paths";

export interface AppServices {
  readonly uploadServices: UploadServices;
  /** Relógio em ms (Date.now no navegador): o período do Histórico conta a partir dele. */
  readonly now: () => number;
}

/**
 * Cada tela é um chunk próprio (`lazy`), baixado só quando a rota abre: o JS inicial fica com o
 * React, o router e a moldura. A 404 fica no chunk de entrada porque a Bancada também a usa e
 * ela é pequena. Acesso entra com a fatia 6, que o sustenta.
 */
export function createRoutes({ uploadServices, now }: AppServices): RouteObject[] {
  return [
    {
      element: <Layout />,
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
