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
  /** Motivo do controle desabilitado; sem ele, o campo está habilitado. */
  readonly disabledHint?: string | null;
}

interface CheckboxSpec {
  readonly field: "webp_lossless" | "make_thumbnails";
  readonly label: string;
  readonly description?: string;
  readonly disabledHint?: string | null;
}

/** Descrição do campo com o motivo, quando desabilitado, no fim (D4). */
function withReason(
  description: string | undefined,
  disabledHint: string | null,
): string | undefined {
  const parts = [description, disabledHint].filter((part) => part !== undefined && part !== null);
  return parts.length === 0 ? undefined : parts.join(" ");
}

/**
 * Formulário do Image Forge v2, escrito à mão (2.2), com defaults e faixas do catálogo. Os
 * controles sem efeito na combinação ficam desabilitados, mas visíveis, como no Media Mill: a caixa
 * em escala, a escala fora dela e as opções de WebP sem saída WebP. Cada um diz o motivo e mostra o
 * valor que vai no envio; o digitado volta quando ele habilita. As de JPEG somem, como na v1,
 * quando nenhuma saída é JPEG.
 */
export function ImageForgeFields({
  settings,
  errors,
  onDraftChange,
}: RecipeFieldsProps<"image-forge", ImageForgeSettings>): ReactElement {
  const [values, setValues] = useState(settings.defaults);
  const { limits, disabledValues } = settings;
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
    disabledHint = null,
  }: TextFieldSpec): ReactElement => {
    const off = disabledHint !== null;
    const shown = withReason(description, disabledHint);
    return (
      <Field
        id={fieldId(field)}
        label={label}
        {...(shown === undefined ? {} : { description: shown })}
        error={errorOf(field)}
      >
        {(control) => (
          <input
            {...control}
            {...(numeric ? { inputMode: "numeric" as const } : {})}
            disabled={off}
            value={off ? disabledValues[field] : values[field]}
            onChange={(event) => {
              update({ ...values, [field]: event.currentTarget.value });
            }}
          />
        )}
      </Field>
    );
  };

  const checkbox = ({
    field,
    label,
    description,
    disabledHint = null,
  }: CheckboxSpec): ReactElement => {
    const off = disabledHint !== null;
    const shown = withReason(description, disabledHint);
    const checked = off && field === "webp_lossless" ? disabledValues.webp_lossless : values[field];
    return (
      <Field
        layout="inline"
        id={fieldId(field)}
        label={label}
        {...(shown === undefined ? {} : { description: shown })}
        error={errorOf(field)}
      >
        {(control) => (
          <input
            {...control}
            type="checkbox"
            disabled={off}
            checked={checked}
            onChange={(event) => {
              update({ ...values, [field]: event.currentTarget.checked });
            }}
          />
        )}
      </Field>
    );
  };
  const boxHint = disabled.box ? T.disabledHints.box : null;
  const webpQualityHint = disabled.webp
    ? T.disabledHints.webpOnly
    : disabled.webpQuality
      ? T.disabledHints.lossless
      : null;

  return (
    <>
      {textField({
        field: "max_width",
        label: T.maxWidth,
        description: T.sideHint(limits.width.min, limits.width.max),
        numeric: true,
        disabledHint: boxHint,
      })}
      {textField({
        field: "max_height",
        label: T.maxHeight,
        description: T.sideHint(limits.height.min, limits.height.max),
        numeric: true,
        disabledHint: boxHint,
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
        description: T.scaleHint(limits.scale.min, limits.scale.max),
        numeric: true,
        disabledHint: disabled.scale ? T.disabledHints.mode(T.fitModes.scale) : null,
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
        description: T.webpQualityHint(limits.webpQuality.min, limits.webpQuality.max),
        numeric: true,
        disabledHint: webpQualityHint,
      })}
      {checkbox({
        field: "webp_lossless",
        label: T.webpLossless,
        disabledHint: disabled.webp ? T.disabledHints.webpOnly : null,
      })}
      {checkbox({
        field: "make_thumbnails",
        label: T.makeThumbnails,
        description: T.makeThumbnailsHint(limits.thumbnailBox),
      })}
    </>
  );
}
