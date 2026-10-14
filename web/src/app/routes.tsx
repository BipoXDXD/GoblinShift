import type { RouteObject } from "react-router";

import { RunPage } from "../runs/RunPage";
import type { UploadServices } from "../upload/useUploadBatch";
import { WorkbenchPage } from "../workbench/WorkbenchPage";
import { HomePage } from "./HomePage";
import { Layout } from "./Layout";
import { NotFoundPage } from "./NotFoundPage";
import { PATHS } from "./paths";

export interface AppServices {
  readonly uploadServices: UploadServices;
}

// Histórico e Acesso entram com as fatias que os sustentam; a Oficina com goblins, com a arte (P11).
export function createRoutes({ uploadServices }: AppServices): RouteObject[] {
  return [
    {
      element: <Layout />,
      children: [
        { path: PATHS.home, element: <HomePage /> },
        { path: PATHS.workbench, element: <WorkbenchPage uploadServices={uploadServices} /> },
        { path: PATHS.run, element: <RunPage /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ];
}
