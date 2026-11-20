import { accessSync, constants, statSync } from "node:fs";
import { isAbsolute } from "node:path";

import type { LaunchOptions } from "@playwright/test";

export function firefoxLaunchOptions(
  executablePath: string | undefined,
  required: boolean,
): LaunchOptions {
  if (!executablePath) {
    if (required) throw new Error("Firefox r1575 exige GS_FIREFOX_EXECUTABLE nas jornadas do CI.");
    return {};
  }
  if (!isAbsolute(executablePath) || !statSync(executablePath).isFile()) {
    throw new Error("GS_FIREFOX_EXECUTABLE precisa apontar para um arquivo executável absoluto.");
  }
  accessSync(executablePath, constants.X_OK);
  return { executablePath };
}
