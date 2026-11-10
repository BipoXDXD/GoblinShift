import { Fragment, useId, useMemo, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router";

import { PATHS } from "../app/paths";
import { Card } from "../components/ui/Card";
import { TEXT } from "../text";
import type { Block, Inline } from "./markdown";

// Links entre os textos de docs/legal/ viram as rotas da interface; outro link relativo aponta
// para o repositório e sai como texto, para não virar uma página quebrada.
const DOCUMENT_ROUTES: Readonly<Record<string, string>> = {
  "termos-de-uso.md": PATHS.terms,
  "aviso-de-privacidade.md": PATHS.privacy,
};

// Com uma ou duas seções o texto cabe de relance, e o sumário seria só ruído antes dele.
const MIN_OUTLINE_SECTIONS = 3;
// Prefixo das âncoras: o título "Conteúdo" não pode colidir com o id do <main> nem com outro id.
const ANCHOR_PREFIX = "secao-";

interface Section {
  readonly blockIndex: number;
  readonly anchor: string;
  readonly title: string;
}

/**
 * Os blocos de um texto legal, com a tipografia de leitura (45 a 75 caracteres por linha). Cada
 * seção (h2) ganha uma âncora tirada do próprio título, e o texto longo abre com um sumário delas.
 */
export function LegalDocument({ blocks }: { readonly blocks: readonly Block[] }): ReactElement {
  const sections = useMemo(() => sectionsOf(blocks), [blocks]);
  const anchors = new Map(sections.map((section) => [section.blockIndex, section.anchor]));
  return (
    <div className="flex max-w-prose flex-col gap-4 leading-relaxed">
      {sections.length >= MIN_OUTLINE_SECTIONS && <Outline sections={sections} />}
      {blocks.map((block, index) => renderBlock(block, index, anchors.get(index)))}
    </div>
  );
}

function Outline({ sections }: { readonly sections: readonly Section[] }): ReactElement {
  const titleId = useId();
  return (
    <Card as="nav" aria-labelledby={titleId}>
      <p id={titleId} className="font-display text-lg font-semibold text-fg">
        {TEXT.legal.outlineTitle}
      </p>
      <ul className="mt-2 flex flex-col">
        {sections.map((section) => (
          <li key={section.anchor}>
            <a href={`#${section.anchor}`} className="inline-flex min-h-11 items-center">
              {section.title}
            </a>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** As seções do texto: os títulos que a tela mostra como h2, com âncoras únicas e estáveis. */
function sectionsOf(blocks: readonly Block[]): Section[] {
  const used = new Map<string, number>();
  const sections: Section[] = [];
  blocks.forEach((block, blockIndex) => {
    if (block.kind !== "heading" || shownLevel(block.level) !== 2) {
      return;
    }
    const title = plainText(block.inlines);
    const base = `${ANCHOR_PREFIX}${slugOf(title)}`;
    const seen = (used.get(base) ?? 0) + 1;
    used.set(base, seen);
    sections.push({ blockIndex, anchor: seen === 1 ? base : `${base}-${String(seen)}`, title });
  });
  return sections;
}

/** "2. O que **não** coletamos" → "2-o-que-nao-coletamos": sem acento, minúsculas, hífens. */
function slugOf(title: string): string {
  const slug = title
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return slug === "" ? "sem-titulo" : slug;
}

function plainText(inlines: readonly Inline[]): string {
  return inlines
    .map((inline) =>
      inline.kind === "text" || inline.kind === "code" ? inline.text : plainText(inline.children),
    )
    .join("");
}

function renderBlock(block: Block, index: number, anchor?: string): ReactElement {
  switch (block.kind) {
    case "heading":
      return <Heading key={index} level={block.level} inlines={block.inlines} anchor={anchor} />;
    case "paragraph":
      return <p key={index}>{renderInlines(block.inlines)}</p>;
    case "quote":
      return (
        <blockquote
          key={index}
          className="flex flex-col gap-2 border-l-4 border-line-soft pl-4 text-fg-muted"
        >
          {block.blocks.map((inner, innerIndex) => renderBlock(inner, innerIndex))}
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

function shownLevel(level: 1 | 2 | 3 | 4): 2 | 3 | 4 {
  return level === 1 ? 2 : level;
}

function Heading({
  level,
  inlines,
  anchor,
}: {
  readonly level: 1 | 2 | 3 | 4;
  readonly inlines: readonly Inline[];
  readonly anchor: string | undefined;
}): ReactElement {
  const shown = shownLevel(level);
  const Tag = HEADING_TAGS[shown];
  return (
    <Tag id={anchor} className={HEADING_CLASSES[shown]}>
      {renderInlines(inlines)}
    </Tag>
  );
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

/** Como um trecho de texto puro aparece: com e-mails clicáveis, ou literal dentro de um link. */
type TextRenderer = (text: string) => ReactNode;

function renderInlines(
  inlines: readonly Inline[],
  renderText: TextRenderer = linkEmails,
): ReactNode {
  return inlines.map((inline, index) => (
    <Fragment key={index}>{renderInline(inline, renderText)}</Fragment>
  ));
}

function renderInline(inline: Inline, renderText: TextRenderer): ReactNode {
  switch (inline.kind) {
    case "text":
      return renderText(inline.text);
    case "strong":
      return (
        <strong className="font-semibold text-fg">
          {renderInlines(inline.children, renderText)}
        </strong>
      );
    case "code":
      return <code className="rounded-lg bg-surface-sunken px-1 text-sm">{inline.text}</code>;
    case "link":
      // Link dentro de link não existe em HTML: o texto de um link fica literal.
      return renderLink(inline.href, renderInlines(inline.children, literal));
  }
}

function literal(text: string): ReactNode {
  return text;
}

// Endereço completo (nome@domínio.tld). "contato@," sem domínio fica texto, e o ponto final da
// frase não entra no link porque o domínio termina numa letra, número ou hífen.
const EMAIL = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g;

/** Os e-mails do texto viram `mailto:`: no celular, o canal de direitos abre o app de e-mail. */
function linkEmails(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(EMAIL)) {
    parts.push(text.slice(last, match.index));
    parts.push(
      <a key={match.index} href={`mailto:${match[0]}`}>
        {match[0]}
      </a>,
    );
    last = match.index + match[0].length;
  }
  if (last === 0) {
    return text;
  }
  parts.push(text.slice(last));
  return parts;
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
