// Roda no processo principal do Playwright, o mesmo que criou a pasta de artefatos (artifactsDir.ts).
import { removeOwnArtifactsDir } from "./support/artifactsDir.ts";

export default function globalTeardown(): void {
  removeOwnArtifactsDir(process.env, process.pid);
}
