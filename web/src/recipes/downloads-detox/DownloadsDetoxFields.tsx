import { useState, type ReactElement } from "react";

import { Field } from "../../components/a11y/Field";
import { FieldGroup } from "../../components/a11y/FieldGroup";
import { TEXT } from "../../text";
import type { RecipeFieldsProps } from "../types";
import {
  fieldId,
  GROUP_BY_OPTIONS,
  NAME_MODES,
  parseDownloadsDetox,
  type DownloadsDetoxFormValues,
  type DownloadsDetoxSettings,
} from "./schema";

const T = TEXT.downloadsDetox;

/**
 * Formulário do Downloads Detox v1, escrito à mão (2.2), com defaults e limites do catálogo. O
 * prefixo só aparece com a numeração, o único modo em que ele vale.
 */
export function DownloadsDetoxFields({
  settings,
  errors,
  onDraftChange,
}: RecipeFieldsProps<"downloads-detox", DownloadsDetoxSettings>): ReactElement {
  const [values, setValues] = useState(settings.defaults);

  const update = (next: DownloadsDetoxFormValues): void => {
    setValues(next);
    onDraftChange(parseDownloadsDetox(next, settings));
  };

  const errorOf = (field: keyof DownloadsDetoxFormValues): string | null =>
    errors.get(fieldId(field)) ?? null;

  return (
    <FieldGroup id="downloads-detox-parametros" legend={T.legend}>
      <Field
        id={fieldId("deduplicate")}
        label={T.deduplicate}
        description={T.deduplicateHint}
        error={errorOf("deduplicate")}
      >
        {(control) => (
          <input
            {...control}
            type="checkbox"
            checked={values.deduplicate}
            onChange={(event) => {
              update({ ...values, deduplicate: event.currentTarget.checked });
            }}
          />
        )}
      </Field>
      <Field id={fieldId("group_by")} label={T.groupBy} error={errorOf("group_by")}>
        {(control) => (
          <select
            {...control}
            value={values.group_by}
            onChange={(event) => {
              const chosen = GROUP_BY_OPTIONS.find(
                (option) => option === event.currentTarget.value,
              );
              if (chosen !== undefined) {
                update({ ...values, group_by: chosen });
              }
            }}
          >
            {GROUP_BY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {T.groupByOptions[option]}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field id={fieldId("name_mode")} label={T.nameMode} error={errorOf("name_mode")}>
        {(control) => (
          <select
            {...control}
            value={values.name_mode}
            onChange={(event) => {
              const chosen = NAME_MODES.find((mode) => mode === event.currentTarget.value);
              if (chosen !== undefined) {
                update({ ...values, name_mode: chosen });
              }
            }}
          >
            {NAME_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {T.nameModes[mode]}
              </option>
            ))}
          </select>
        )}
      </Field>
      {values.name_mode === "indexed" && (
        <Field
          id={fieldId("name_prefix")}
          label={T.namePrefix}
          description={T.namePrefixHint(settings.limits.maxPrefixChars)}
          error={errorOf("name_prefix")}
        >
          {(control) => (
            <input
              {...control}
              value={values.name_prefix}
              onChange={(event) => {
                update({ ...values, name_prefix: event.currentTarget.value });
              }}
            />
          )}
        </Field>
      )}
    </FieldGroup>
  );
}
