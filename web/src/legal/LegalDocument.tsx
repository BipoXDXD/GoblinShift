import { Fragment, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router";

import { PATHS } from "../app/paths";
import type { Block, Inline } from "./markdown";

// Links entre os textos de docs/legal/ viram as rotas da interface; outro link relativo aponta
// para o repositório e sai como texto, para não virar uma página quebrada.
const DOCUMENT_ROUTES: Readonly<Record<string, string>> = {
  "termos-de-uso.md": PATHS.terms,
  "aviso-de-privacidade.md": PATHS.privacy,
};

/** Os blocos de um texto legal, com a tipografia de leitura (45 a 75 caracteres por linha). */
export function LegalDocument({ blocks }: { readonly blocks: readonly Block[] }): ReactElement {
  return (
    <div className="flex max-w-prose flex-col gap-4 leading-relaxed">{blocks.map(renderBlock)}</div>
  );
}

function renderBlock(block: Block, index: number): ReactElement {
  switch (block.kind) {
    case "heading":
      return <Heading key={index} level={block.level} inlines={block.inlines} />;
    case "paragraph":
      return <p key={index}>{renderInlines(block.inlines)}</p>;
    case "quote":
      return (
        <blockquote
          key={index}
          className="flex flex-col gap-2 border-l-4 border-line-soft pl-4 text-fg-muted"
        >
          {block.blocks.map(renderBlock)}
        </blockquote>
      );
    case "list": {
      const List = block.ordered ? "ol" : "ul";
      return (
        <List
          key={index}
          className={`flex flex-col gap-2 pl-6 ${block.ordered ? "list-decimal" : "list-disc"}`}
        >
          {block.items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInlines(item)}</li>
          ))}
        </List>
      );
    }
    case "table":
      return <Table key={index} header={block.header} rows={block.rows} />;
    case "rule":
      return <hr key={index} className="border-line-soft" />;
  }
}

// O h1 do texto vira o título da tela (LegalPage); aqui os níveis ficam como no arquivo.
const HEADING_TAGS = { 2: "h2", 3: "h3", 4: "h4" } as const;
const HEADING_CLASSES = {
  2: "mt-6 font-display text-xl font-semibold text-fg",
  3: "mt-4 text-lg font-semibold text-fg",
  4: "font-semibold text-fg",
} as const;

function Heading({
  level,
  inlines,
}: {
  readonly level: 1 | 2 | 3 | 4;
  readonly inlines: readonly Inline[];
}): ReactElement {
  const shown = level === 1 ? 2 : level;
  const Tag = HEADING_TAGS[shown];
  return <Tag className={HEADING_CLASSES[shown]}>{renderInlines(inlines)}</Tag>;
}

function Table({
  header,
  rows,
}: {
  readonly header: readonly (readonly Inline[])[];
  readonly rows: readonly (readonly (readonly Inline[])[])[];
}): ReactElement {
  return (
    // Tabela larga rola dentro da própria caixa, não a página (WCAG 1.4.10); com foco, rola pelo
    // teclado (regra scrollable-region-focusable do axe).
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- caixa rolável precisa de foco para o teclado
    <div tabIndex={0} className="overflow-x-auto rounded-lg">
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr>
            {header.map((cell, index) => (
              <th
                key={index}
                scope="col"
                className="border-b-2 border-line px-3 py-2 font-semibold"
              >
                {renderInlines(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, index) => (
                <td key={index} className="border-b border-line-soft px-3 py-2 align-top">
                  {renderInlines(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderInlines(inlines: readonly Inline[]): ReactNode {
  return inlines.map((inline, index) => <Fragment key={index}>{renderInline(inline)}</Fragment>);
}

function renderInline(inline: Inline): ReactNode {
  switch (inline.kind) {
    case "text":
      return inline.text;
    case "strong":
      return <strong className="font-semibold text-fg">{renderInlines(inline.children)}</strong>;
    case "code":
      return <code className="rounded-lg bg-surface-sunken px-1 text-sm">{inline.text}</code>;
    case "link":
      return renderLink(inline.href, renderInlines(inline.children));
  }
}

function renderLink(href: string, children: ReactNode): ReactNode {
  const route = DOCUMENT_ROUTES[href];
  if (route !== undefined) {
    return <Link to={route}>{children}</Link>;
  }
  if (href.startsWith("https://")) {
    return (
      <a href={href} rel="noreferrer">
        {children}
      </a>
    );
  }
  return children;
}
