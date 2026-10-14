import { useEffect, useRef, useState, type ReactElement } from "react";

import { Field } from "../../components/a11y/Field";
import { FieldGroup } from "../../components/a11y/FieldGroup";
import { TEXT } from "../../text";
import type { RecipeFieldsProps } from "../types";
import {
  addButtonId,
  CASE_MODES,
  DELIMITERS,
  FILTER_OPS,
  FORMULA_GUARDS,
  parseCsvJanitor,
  rowFieldId,
  singleFieldId,
  type CaseRuleRow,
  type CsvJanitorFormValues,
  type FilterRow,
} from "./schema";
import type { CsvJanitorSettings } from "./settings";

const T = TEXT.csvJanitor;

type ColumnListField = "trim_columns" | "deduplicate_by" | "output_columns";
type OptionField = "input_delimiter" | "output_delimiter" | "formula_guard";

interface OptionSelect<F extends OptionField> {
  readonly field: F;
  readonly label: string;
  readonly options: readonly CsvJanitorFormValues[F][];
  readonly labels: Readonly<Record<CsvJanitorFormValues[F], string>>;
}

const COLUMN_LISTS: readonly { field: ColumnListField; label: string; hint: string }[] = [
  { field: "trim_columns", label: T.trimColumns, hint: T.trimColumnsHint },
  { field: "deduplicate_by", label: T.deduplicateBy, hint: T.deduplicateByHint },
  { field: "output_columns", label: T.outputColumns, hint: T.outputColumnsHint },
];

/**
 * Formulário da CSV Janitor v1, escrito à mão (2.2), com defaults e limites do catálogo. Guarda os próprios valores e entrega à Bancada,
 * a cada mudança, o comando validado ou os erros. Adicionar uma linha leva o foco a ela; remover
 * devolve o foco ao botão de adicionar.
 */
export function CsvJanitorFields({
  settings,
  errors,
  onDraftChange,
}: RecipeFieldsProps<"csv-janitor", CsvJanitorSettings>): ReactElement {
  const [values, setValues] = useState(settings.defaults);
  const { limits } = settings;
  const pendingFocus = useRef<string | null>(null);

  useEffect(() => {
    if (pendingFocus.current !== null) {
      document.getElementById(pendingFocus.current)?.focus();
      pendingFocus.current = null;
    }
  });

  const update = (next: CsvJanitorFormValues, focusId: string | null = null): void => {
    pendingFocus.current = focusId;
    setValues(next);
    onDraftChange(parseCsvJanitor(next, limits));
  };

  const errorOf = (id: string): string | null => errors.get(id) ?? null;

  const optionSelect = <F extends OptionField>({
    field,
    label,
    options,
    labels,
  }: OptionSelect<F>): ReactElement => (
    <Field id={singleFieldId(field)} label={label} error={errorOf(singleFieldId(field))}>
      {(control) => (
        <select
          {...control}
          value={values[field]}
          onChange={(event) => {
            const chosen = options.find((option) => option === event.currentTarget.value);
            if (chosen !== undefined) {
              update({ ...values, [field]: chosen });
            }
          }}
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {labels[option]}
            </option>
          ))}
        </select>
      )}
    </Field>
  );

  return (
    <FieldGroup id="csv-janitor-parametros" legend={T.legend}>
      {optionSelect({
        field: "input_delimiter",
        label: T.inputDelimiter,
        options: DELIMITERS,
        labels: T.delimiters,
      })}
      {optionSelect({
        field: "output_delimiter",
        label: T.outputDelimiter,
        options: DELIMITERS,
        labels: T.delimiters,
      })}
      {COLUMN_LISTS.map(({ field, label, hint }) => (
        <Field
          key={field}
          id={singleFieldId(field)}
          label={label}
          description={`${hint} ${T.columnListHint}`}
          error={errorOf(singleFieldId(field))}
        >
          {(control) => (
            <textarea
              {...control}
              value={values[field]}
              onChange={(event) => {
                update({ ...values, [field]: event.currentTarget.value });
              }}
            />
          )}
        </Field>
      ))}
      <CaseRules
        rows={values.case_rules}
        max={limits.maxColumns}
        errorOf={errorOf}
        onChange={(case_rules, focusId) => {
          update({ ...values, case_rules }, focusId);
        }}
      />
      <Filters
        rows={values.filters}
        max={limits.maxFilters}
        errorOf={errorOf}
        onChange={(filters, focusId) => {
          update({ ...values, filters }, focusId);
        }}
      />
      {optionSelect({
        field: "formula_guard",
        label: T.formulaGuard,
        options: FORMULA_GUARDS,
        labels: T.formulaGuards,
      })}
    </FieldGroup>
  );
}

interface RowsProps<Row> {
  readonly rows: readonly Row[];
  readonly max: number;
  readonly errorOf: (id: string) => string | null;
  readonly onChange: (rows: readonly Row[], focusId: string | null) => void;
}

function CaseRules({ rows, max, errorOf, onChange }: RowsProps<CaseRuleRow>): ReactElement {
  const list = useRowList("case_rules", { rows, onChange, max });
  return (
    <FieldGroup
      id="csv-janitor-case_rules"
      legend={T.caseRules}
      description={T.caseRulesHint}
      error={errorOf(addButtonId("case_rules"))}
    >
      <ol>
        {rows.map((row, index) => (
          <li key={row.key}>
            <ColumnInput
              id={rowFieldId("case_rules", index, "column")}
              row={index + 1}
              value={row.column}
              error={errorOf(rowFieldId("case_rules", index, "column"))}
              onChange={(column) => {
                list.replace(index, { ...row, column });
              }}
            />
            <Field
              id={rowFieldId("case_rules", index, "mode")}
              label={T.mode(index + 1)}
              error={errorOf(rowFieldId("case_rules", index, "mode"))}
            >
              {(control) => (
                <select
                  {...control}
                  value={row.mode}
                  onChange={(event) => {
                    list.replace(index, { ...row, mode: modeFrom(event.currentTarget.value) });
                  }}
                >
                  {CASE_MODES.map((mode) => (
                    <option key={mode} value={mode}>
                      {T.caseModes[mode]}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <RemoveRowButton
              row={index + 1}
              onRemove={() => {
                list.remove(index);
              }}
            />
          </li>
        ))}
      </ol>
      <AddRowButton
        id={addButtonId("case_rules")}
        full={list.full}
        onAdd={() => {
          list.add({ key: crypto.randomUUID(), column: "", mode: "lower" }, "column");
        }}
      >
        {T.addCaseRule}
      </AddRowButton>
    </FieldGroup>
  );
}

function Filters({ rows, max, errorOf, onChange }: RowsProps<FilterRow>): ReactElement {
  const list = useRowList("filters", { rows, onChange, max });
  return (
    <FieldGroup
      id="csv-janitor-filters"
      legend={T.filters}
      description={T.filtersHint}
      error={errorOf(addButtonId("filters"))}
    >
      <ol>
        {rows.map((row, index) => (
          <li key={row.key}>
            <ColumnInput
              id={rowFieldId("filters", index, "column")}
              row={index + 1}
              value={row.column}
              error={errorOf(rowFieldId("filters", index, "column"))}
              onChange={(column) => {
                list.replace(index, { ...row, column });
              }}
            />
            <Field
              id={rowFieldId("filters", index, "op")}
              label={T.op(index + 1)}
              error={errorOf(rowFieldId("filters", index, "op"))}
            >
              {(control) => (
                <select
                  {...control}
                  value={row.op}
                  onChange={(event) => {
                    list.replace(index, { ...row, op: opFrom(event.currentTarget.value) });
                  }}
                >
                  {FILTER_OPS.map((op) => (
                    <option key={op} value={op}>
                      {T.filterOps[op]}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            {row.op !== "is_empty" && (
              <Field
                id={rowFieldId("filters", index, "value")}
                label={T.value(index + 1)}
                error={errorOf(rowFieldId("filters", index, "value"))}
              >
                {(control) => (
                  <input
                    {...control}
                    value={row.value}
                    onChange={(event) => {
                      list.replace(index, { ...row, value: event.currentTarget.value });
                    }}
                  />
                )}
              </Field>
            )}
            <RemoveRowButton
              row={index + 1}
              onRemove={() => {
                list.remove(index);
              }}
            />
          </li>
        ))}
      </ol>
      <AddRowButton
        id={addButtonId("filters")}
        full={list.full}
        onAdd={() => {
          list.add({ key: crypto.randomUUID(), column: "", op: "eq", value: "" }, "column");
        }}
      >
        {T.addFilter}
      </AddRowButton>
    </FieldGroup>
  );
}

interface RowListOptions<Row> {
  readonly rows: readonly Row[];
  readonly onChange: RowsProps<Row>["onChange"];
  readonly max: number;
}

function useRowList<Row>(listField: string, { rows, onChange, max }: RowListOptions<Row>) {
  return {
    full: rows.length >= max,
    add: (row: Row, focusPart: string) => {
      if (rows.length < max) {
        onChange([...rows, row], rowFieldId(listField, rows.length, focusPart));
      }
    },
    replace: (index: number, row: Row) => {
      onChange(
        rows.map((current, position) => (position === index ? row : current)),
        null,
      );
    },
    remove: (index: number) => {
      onChange(
        rows.filter((_, position) => position !== index),
        addButtonId(listField),
      );
    },
  };
}

interface ColumnInputProps {
  readonly id: string;
  readonly row: number;
  readonly value: string;
  readonly error: string | null;
  readonly onChange: (column: string) => void;
}

function ColumnInput({ id, row, value, error, onChange }: ColumnInputProps): ReactElement {
  return (
    <Field id={id} label={T.column(row)} error={error}>
      {(control) => (
        <input
          {...control}
          value={value}
          onChange={(event) => {
            onChange(event.currentTarget.value);
          }}
        />
      )}
    </Field>
  );
}

interface AddRowButtonProps {
  readonly id: string;
  readonly full: boolean;
  readonly onAdd: () => void;
  readonly children: string;
}

// aria-disabled em vez de disabled: o botão continua focável e anunciado quando a lista enche.
function AddRowButton({ id, full, onAdd, children }: AddRowButtonProps): ReactElement {
  return (
    <button id={id} type="button" aria-disabled={full} onClick={onAdd}>
      {children}
    </button>
  );
}

function RemoveRowButton({ row, onRemove }: { row: number; onRemove: () => void }): ReactElement {
  return (
    <button type="button" onClick={onRemove}>
      {T.removeRow(row)}
    </button>
  );
}

function modeFrom(value: string): CaseRuleRow["mode"] {
  return CASE_MODES.find((mode) => mode === value) ?? "lower";
}

function opFrom(value: string): FilterRow["op"] {
  return FILTER_OPS.find((op) => op === value) ?? "eq";
}
