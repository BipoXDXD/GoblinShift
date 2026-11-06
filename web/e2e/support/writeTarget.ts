const LOOPBACK_HOSTS: ReadonlySet<string> = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Fora do modo VPS as specs escrevem de verdade: só o Compose local pode receber isso. */
export function assertLocalWriteTarget(baseUrl: string): void {
  if (!LOOPBACK_HOSTS.has(new URL(baseUrl).hostname)) {
    throw new Error(
      `Sem E2E_VPS=1 as specs escrevem na API: E2E_BASE_URL precisa ser local, não ${baseUrl}.`,
    );
  }
}
