import { useEffect, useId, useRef, useState, type ReactElement } from "react";

import { Field } from "../../components/a11y/Field";
import { FieldGroup } from "../../components/a11y/FieldGroup";
import { moveItem, ReorderableList } from "../../components/a11y/ReorderableList";
import { Button } from "../../components/ui/Button";
import { TEXT } from "../../text";
import type { RecipeFieldsProps, RecipeInput } from "../types";
import {
  addCardButtonId,
  cardFieldId,
  fieldId,
  OPERATIONS,
  pageMapPreview,
  parsePdfPress,
  type PageCard,
  type PdfPressFormValues,
  type PdfPressSettings,
} from "./schema";

const T = TEXT.pdfPress;

/**
 * Formulário do PDF Press v1, escrito à mão (2.2). A seleção de páginas é uma lista de cartões
 * numerados, reordenável por botões (WCAG 2.5.7) e por arrasto, sem renderizar PDF: o navegador
 * não abre o arquivo, e a prévia é o mapa de páginas que sairá.
 *
 * O rascunho depende dos PDFs prontos (um cartão só vale com um deles), então ele é recalculado
 * quando os valores ou a lista de PDFs mudam.
 */
export function PdfPressFields({
  settings,
  errors,
  onDraftChange,
  inputs,
}: RecipeFieldsProps<"pdf-press", PdfPressSettings>): ReactElement {
  const [values, setValues] = useState(settings.defaults);
  const pendingFocus = useRef<string | null>(null);

  useEffect(() => {
    onDraftChange(parsePdfPress(values, settings, inputs));
  }, [values, settings, inputs, onDraftChange]);

  useEffect(() => {
    if (pendingFocus.current !== null) {
      document.getElementById(pendingFocus.current)?.focus();
      pendingFocus.current = null;
    }
  });

  const errorOf = (id: string): string | null => errors.get(id) ?? null;
  const setCards = (cards: readonly PageCard[], focusId: string | null = null): void => {
    pendingFocus.current = focusId;
    setValues({ ...values, cards });
  };

  return (
    <>
      <Field id={fieldId("operation")} label={T.operation} error={errorOf(fieldId("operation"))}>
        {(control) => (
          <select
            {...control}
            value={values.operation}
            onChange={(event) => {
              const chosen = OPERATIONS.find((option) => option === event.currentTarget.value);
              if (chosen !== undefined) {
                setValues({ ...values, operation: chosen });
              }
            }}
          >
            {OPERATIONS.map((option) => (
              <option key={option} value={option}>
                {T.operations[option]}
              </option>
            ))}
          </select>
        )}
      </Field>
      {values.operation === "merge" && <p className={HINT}>{T.mergeHint}</p>}
      {values.operation === "split" && (
        <Field
          id={fieldId("pages_per_document")}
          label={T.pagesPerDocument}
          description={T.pagesPerDocumentHint(settings.limits.maxPages)}
          error={errorOf(fieldId("pages_per_document"))}
        >
          {(control) => (
            <input
              {...control}
              inputMode="numeric"
              value={values.pages_per_document}
              onChange={(event) => {
                setValues({ ...values, pages_per_document: event.currentTarget.value });
              }}
            />
          )}
        </Field>
      )}
      {values.operation === "select" && (
        <PageCards
          cards={values.cards}
          inputs={inputs}
          maxCards={settings.limits.maxSelectedPages}
          errorOf={errorOf}
          onChange={setCards}
        />
      )}
      <PageMapPreview values={values} inputs={inputs} />
    </>
  );
}

const HINT = "max-w-prose text-sm leading-relaxed text-fg-muted";

interface PageCardsProps {
  readonly cards: readonly PageCard[];
  readonly inputs: readonly RecipeInput[];
  readonly maxCards: number;
  readonly errorOf: (id: string) => string | null;
  readonly onChange: (cards: readonly PageCard[], focusId?: string | null) => void;
}

function PageCards({ cards, inputs, maxCards, errorOf, onChange }: PageCardsProps): ReactElement {
  const positions = new Map(cards.map((card, index) => [card.key, index]));
  const full = cards.length >= maxCards;
  const positionOf = (card: PageCard): number => positions.get(card.key) ?? 0;

  const replace = (index: number, card: PageCard): void => {
    onChange(cards.map((current, position) => (position === index ? card : current)));
  };

  return (
    <FieldGroup
      id="pdf-press-select"
      legend={T.select}
      description={T.selectHint(maxCards)}
      error={errorOf(addCardButtonId())}
    >
      {inputs.length === 0 && <p className={HINT}>{T.selectWaitingFiles}</p>}
      {cards.length > 0 && (
        <ReorderableList
          label={T.selectOrder}
          items={cards}
          getKey={(card) => card.key}
          getName={(card) => T.cardName(positionOf(card) + 1)}
          renderItem={(card) => (
            <PageCardView
              card={card}
              index={positionOf(card)}
              inputs={inputs}
              errorOf={errorOf}
              onChange={(next) => {
                replace(positionOf(card), next);
              }}
              onRemove={() => {
                onChange(
                  cards.filter((other) => other.key !== card.key),
                  addCardButtonId(),
                );
              }}
            />
          )}
          onMove={(move) => {
            onChange(moveItem(cards, move));
          }}
        />
      )}
      <div>
        <Button
          id={addCardButtonId()}
          type="button"
          variant="secondary"
          aria-disabled={full}
          onClick={() => {
            if (!full) {
              const card = {
                key: crypto.randomUUID(),
                uploadId: inputs[0]?.uploadId ?? "",
                pageNumber: "1",
              };
              onChange([...cards, card], cardFieldId(cards.length, "page_number"));
            }
          }}
        >
          {T.addCard}
        </Button>
      </div>
    </FieldGroup>
  );
}

interface PageCardViewProps {
  readonly card: PageCard;
  readonly index: number;
  readonly inputs: readonly RecipeInput[];
  readonly errorOf: (id: string) => string | null;
  readonly onChange: (card: PageCard) => void;
  readonly onRemove: () => void;
}

function PageCardView({
  card,
  index,
  inputs,
  errorOf,
  onChange,
  onRemove,
}: PageCardViewProps): ReactElement {
  const fileId = cardFieldId(index, "upload_id");
  const pageId = cardFieldId(index, "page_number");
  const number = index + 1;
  return (
    <div className="flex flex-col gap-3">
      <p className="font-display text-lg font-semibold text-fg">{T.cardTitle(number)}</p>
      <Field id={fileId} label={T.cardFile(number)} error={errorOf(fileId)}>
        {(control) => (
          <select
            {...control}
            value={card.uploadId}
            onChange={(event) => {
              onChange({ ...card, uploadId: event.currentTarget.value });
            }}
          >
            {!inputs.some((input) => input.uploadId === card.uploadId) && (
              <option value={card.uploadId}>{T.noFileChosen}</option>
            )}
            {inputs.map((input) => (
              <option key={input.uploadId} value={input.uploadId}>
                {input.name}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field id={pageId} label={T.cardPage(number)} error={errorOf(pageId)}>
        {(control) => (
          <input
            {...control}
            inputMode="numeric"
            value={card.pageNumber}
            onChange={(event) => {
              onChange({ ...card, pageNumber: event.currentTarget.value });
            }}
          />
        )}
      </Field>
      <div>
        <Button type="button" variant="tertiary" onClick={onRemove}>
          {T.removeCard(number)}
        </Button>
      </div>
    </div>
  );
}

function PageMapPreview({
  values,
  inputs,
}: {
  readonly values: PdfPressFormValues;
  readonly inputs: readonly RecipeInput[];
}): ReactElement {
  const headingId = useId();
  const documents = pageMapPreview(values, inputs);
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h3 id={headingId} className="text-sm font-semibold text-fg">
        {T.preview.heading}
      </h3>
      {documents.length === 0 ? (
        <p className={HINT}>{T.preview.empty}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {documents.map((document, index) => (
            <li key={`${document.name}-${String(index)}`} className="flex flex-col gap-1">
              <span className="font-mono text-sm text-fg">{document.name}</span>
              <ol className="list-decimal pl-6 text-sm text-fg-muted">
                {document.entries.map((entry, position) => (
                  <li key={`${entry}-${String(position)}`}>{entry}</li>
                ))}
              </ol>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
