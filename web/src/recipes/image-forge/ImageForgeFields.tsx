import { useState, type ReactElement } from "react";

import { Field } from "../../components/a11y/Field";
import { FieldGroup } from "../../components/a11y/FieldGroup";
import { TEXT } from "../../text";
import type { RecipeFieldsProps } from "../types";
import {
  fieldId,
  OUTPUT_FORMATS,
  parseImageForge,
  type ImageForgeFormValues,
  type ImageForgeSettings,
} from "./schema";

const T = TEXT.imageForge;

interface TextFieldSpec {
  readonly field: "max_width" | "max_height" | "jpeg_quality" | "jpeg_background";
  readonly label: string;
  readonly description: string;
  readonly numeric: boolean;
}

/**
 * Formulário do Image Forge v1, escrito à mão (2.2), com defaults e faixas do catálogo. As opções
 * de JPEG somem com saída PNG, que não gera JPEG.
 */
export function ImageForgeFields({
  settings,
  errors,
  onDraftChange,
}: RecipeFieldsProps<"image-forge", ImageForgeSettings>): ReactElement {
  const [values, setValues] = useState(settings.defaults);
  const { limits } = settings;

  const update = (next: ImageForgeFormValues): void => {
    setValues(next);
    onDraftChange(parseImageForge(next, settings));
  };

  const textField = ({ field, label, description, numeric }: TextFieldSpec): ReactElement => (
    <Field
      id={fieldId(field)}
      label={label}
      description={description}
      error={errors.get(fieldId(field)) ?? null}
    >
      {(control) => (
        <input
          {...control}
          {...(numeric ? { inputMode: "numeric" as const } : {})}
          value={values[field]}
          onChange={(event) => {
            update({ ...values, [field]: event.currentTarget.value });
          }}
        />
      )}
    </Field>
  );

  return (
    <FieldGroup id="image-forge-parametros" legend={T.legend}>
      {textField({
        field: "max_width",
        label: T.maxWidth,
        description: T.sideHint(limits.width.min, limits.width.max),
        numeric: true,
      })}
      {textField({
        field: "max_height",
        label: T.maxHeight,
        description: T.sideHint(limits.height.min, limits.height.max),
        numeric: true,
      })}
      <Field
        id={fieldId("output_format")}
        label={T.outputFormat}
        error={errors.get(fieldId("output_format")) ?? null}
      >
        {(control) => (
          <select
            {...control}
            value={values.output_format}
            onChange={(event) => {
              const chosen = OUTPUT_FORMATS.find((format) => format === event.currentTarget.value);
              if (chosen !== undefined) {
                update({ ...values, output_format: chosen });
              }
            }}
          >
            {OUTPUT_FORMATS.map((format) => (
              <option key={format} value={format}>
                {T.outputFormats[format]}
              </option>
            ))}
          </select>
        )}
      </Field>
      {values.output_format !== "png" && (
        <>
          {textField({
            field: "jpeg_quality",
            label: T.jpegQuality,
            description: T.jpegQualityHint(limits.quality.min, limits.quality.max),
            numeric: true,
          })}
          {textField({
            field: "jpeg_background",
            label: T.jpegBackground,
            description: T.jpegBackgroundHint,
            numeric: false,
          })}
        </>
      )}
      <Field
        id={fieldId("make_thumbnails")}
        label={T.makeThumbnails}
        description={T.makeThumbnailsHint(limits.thumbnailBox)}
        error={errors.get(fieldId("make_thumbnails")) ?? null}
      >
        {(control) => (
          <input
            {...control}
            type="checkbox"
            checked={values.make_thumbnails}
            onChange={(event) => {
              update({ ...values, make_thumbnails: event.currentTarget.checked });
            }}
          />
        )}
      </Field>
    </FieldGroup>
  );
}
