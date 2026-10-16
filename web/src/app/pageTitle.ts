/** O `handle` das rotas que dão título à aba (routes.tsx). */
export interface PageTitleHandle {
  readonly title: string;
}

/** O título do `handle` de uma rota, ou `null` se ela não define um. */
export function pageTitleOf(handle: unknown): string | null {
  if (typeof handle !== "object" || handle === null || !("title" in handle)) {
    return null;
  }
  return typeof handle.title === "string" ? handle.title : null;
}
