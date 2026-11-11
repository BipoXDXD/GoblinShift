import { TEXT } from "../text";

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

/** O título da aba: o da tela antes da marca, ou só a marca. */
export function documentTitle(title: string | undefined): string {
  return title === undefined ? TEXT.layout.brand : `${title} · ${TEXT.layout.brand}`;
}
