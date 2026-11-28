import { useState, type ReactElement } from "react";

import { Field } from "../../components/a11y/Field";
import { FieldGroup } from "../../components/a11y/FieldGroup";
import { TEXT } from "../../text";
import type { RecipeFieldsProps } from "../types";
import {
  disabledControls,
  fieldId,
  FIT_MODES,
  OUTPUT_FORMATS,
  parseImageForge,
  type ImageForgeFormValues,
  type ImageForgeSettings,
} from "./schema";

const T = TEXT.imageForge;

interface TextFieldSpec {
  readonly field:
    | "max_width"
    | "max_height"
    | "jpeg_quality"
    | "jpeg_background"
    | "scale_percent"
    | "webp_quality";
  readonly label: string;
  readonly description?: string;
  readonly numeric: boolean;
  readonly disabled?: boolean;
}

interface CheckboxSpec {
  readonly field: "webp_lossless" | "make_thumbnails";
  readonly label: string;
  readonly description?: string;
  readonly disabled: boolean;
}

/**
 * Formulário do Image Forge v2, escrito à mão (2.2), com defaults e faixas do catálogo. Os
 * controles sem efeito na combinação ficam desabilitados, mas visíveis, como no Media Mill: a caixa
 * em escala, a escala fora dela e as opções de WebP sem saída WebP. As de JPEG somem, como na v1,
 * quando nenhuma saída é JPEG.
 */
export function ImageForgeFields({
  settings,
  errors,
  onDraftChange,
}: RecipeFieldsProps<"image-forge", ImageForgeSettings>): ReactElement {
  const [values, setValues] = useState(settings.defaults);
  const { limits } = settings;
  const disabled = disabledControls(values);

  const update = (next: ImageForgeFormValues): void => {
    setValues(next);
    onDraftChange(parseImageForge(next, settings));
  };
  const errorOf = (field: keyof ImageForgeFormValues): string | null =>
    errors.get(fieldId(field)) ?? null;

  const textField = ({
    field,
    label,
    description,
    numeric,
    disabled: off = false,
  }: TextFieldSpec): ReactElement => (
    <Field
      id={fieldId(field)}
      label={label}
      {...(description === undefined ? {} : { description })}
      error={errorOf(field)}
    >
      {(control) => (
        <input
          {...control}
          {...(numeric ? { inputMode: "numeric" as const } : {})}
          disabled={off}
          value={values[field]}
          onChange={(event) => {
            update({ ...values, [field]: event.currentTarget.value });
          }}
        />
      )}
    </Field>
  );

  const checkbox = ({ field, label, description, disabled: off }: CheckboxSpec): ReactElement => (
    <Field
      layout="inline"
      id={fieldId(field)}
      label={label}
      {...(description === undefined ? {} : { description })}
      error={errorOf(field)}
    >
      {(control) => (
        <input
          {...control}
          type="checkbox"
          disabled={off}
          checked={values[field]}
          onChange={(event) => {
            update({ ...values, [field]: event.currentTarget.checked });
          }}
        />
      )}
    </Field>
  );

  return (
    <>
      {textField({
        field: "max_width",
        label: T.maxWidth,
        description: T.sideHint(limits.width.min, limits.width.max),
        numeric: true,
        disabled: disabled.box,
      })}
      {textField({
        field: "max_height",
        label: T.maxHeight,
        description: T.sideHint(limits.height.min, limits.height.max),
        numeric: true,
        disabled: disabled.box,
      })}
      <Field id={fieldId("output_format")} label={T.outputFormat} error={errorOf("output_format")}>
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
      <FieldGroup id={fieldId("fit")} legend={T.fit} error={errorOf("fit")}>
        {FIT_MODES.map((mode, index) => (
          <Field
            key={mode}
            layout="inline"
            id={index === 0 ? fieldId("fit") : `${fieldId("fit")}-${mode}`}
            label={T.fitModes[mode]}
            {...(mode === "cover" || mode === "pad" ? { description: T.fitHints[mode] } : {})}
          >
            {(control) => (
              <input
                {...control}
                type="radio"
                name={fieldId("fit")}
                value={mode}
                checked={values.fit === mode}
                onChange={() => {
                  update({ ...values, fit: mode });
                }}
              />
            )}
          </Field>
        ))}
      </FieldGroup>
      {textField({
        field: "scale_percent",
        label: T.scalePercent,
        numeric: true,
        disabled: disabled.scale,
      })}
      {!disabled.jpeg && (
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
      {textField({
        field: "webp_quality",
        label: T.webpQuality,
        numeric: true,
        disabled: disabled.webpQuality,
      })}
      {checkbox({ field: "webp_lossless", label: T.webpLossless, disabled: disabled.webp })}
      {checkbox({
        field: "make_thumbnails",
        label: T.makeThumbnails,
        description: T.makeThumbnailsHint(limits.thumbnailBox),
        disabled: false,
      })}
    </>
  );
}
