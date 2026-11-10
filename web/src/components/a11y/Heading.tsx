import { createElement, type ComponentPropsWithoutRef, type ReactElement } from "react";

/** Nível do título de um componente aninhado; o `h1` é da página (`FocusHeading`). */
export type HeadingLevel = 2 | 3 | 4;

// Ligada à união: um nível novo sem tag não compila, em vez de cair em outro nível em silêncio.
const HEADING_TAG = { 2: "h2", 3: "h3", 4: "h4" } as const satisfies Record<
  HeadingLevel,
  `h${HeadingLevel}`
>;

interface HeadingProps extends ComponentPropsWithoutRef<"h2"> {
  readonly level: HeadingLevel;
}

/** Título no nível que a seção onde o componente aparece pede. */
export function Heading({ level, ...props }: HeadingProps): ReactElement {
  return createElement(HEADING_TAG[level], props);
}
