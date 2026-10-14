import type { RouteObject } from "react-router";

import { RunPage } from "../runs/RunPage";
import type { UploadServices } from "../upload/useUploadBatch";
import { WorkbenchPage } from "../workbench/WorkbenchPage";
import { HomePage } from "./HomePage";
import { NotFoundPage } from "./NotFoundPage";
import { PATHS } from "./paths";

export interface AppServices {
  readonly uploadServices: UploadServices;
}

// Histórico, Acesso e a Oficina com goblins entram depois das decisões de design (D1–D12).
export function createRoutes({ uploadServices }: AppServices): RouteObject[] {
  return [
    { path: PATHS.home, element: <HomePage /> },
    { path: PATHS.workbench, element: <WorkbenchPage uploadServices={uploadServices} /> },
    { path: PATHS.run, element: <RunPage /> },
    { path: "*", element: <NotFoundPage /> },
  ];
}
