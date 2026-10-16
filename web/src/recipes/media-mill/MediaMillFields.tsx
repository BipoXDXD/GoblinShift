import { useState, type ReactElement } from "react";

import { Field } from "../../components/a11y/Field";
import { FieldGroup } from "../../components/a11y/FieldGroup";
import { TEXT } from "../../text";
import type { RecipeFieldsProps } from "../types";
import {
  AUDIO_FORMATS,
  fieldId,
  parseMediaMill,
  THUMBNAIL_COUNTS,
  type MediaMillFormValues,
  type MediaMillSettings,
} from "./schema";

const T = TEXT.mediaMill;
const HZ_PER_KHZ = 1000;

/**
 * Formulário do Media Mill v1, escrito à mão (2.2), com defaults e limites do catálogo. Formato e
 * faixa de áudio ficam desabilitados, mas visíveis, sem a extração: a pessoa vê o que existe.
 */
export function MediaMillFields({
  settings,
  errors,
  onDraftChange,
}: RecipeFieldsProps<"media-mill", MediaMillSettings>): ReactElement {
  const [values, setValues] = useState(settings.defaults);
  const { limits } = settings;

  const update = (next: MediaMillFormValues): void => {
    setValues(next);
    onDraftChange(parseMediaMill(next, settings));
  };
  const errorOf = (field: keyof MediaMillFormValues): string | null =>
    errors.get(fieldId(field)) ?? null;
  const audioOff = !values.extract_audio;

  return (
    <>
      <Field
        layout="inline"
        id={fieldId("extract_audio")}
        label={T.extractAudio}
        description={T.extractAudioHint(
          new Intl.NumberFormat(TEXT.locale).format(limits.maxSampleRateHz / HZ_PER_KHZ),
          limits.maxChannels,
        )}
        error={errorOf("extract_audio")}
      >
        {(control) => (
          <input
            {...control}
            type="checkbox"
            checked={values.extract_audio}
            onChange={(event) => {
              update({ ...values, extract_audio: event.currentTarget.checked });
            }}
          />
        )}
      </Field>
      <FieldGroup
        id={fieldId("audio_format")}
        legend={T.audioFormat}
        description={audioOff ? `${T.audioFormatHint} ${T.audioDisabledHint}` : T.audioFormatHint}
        error={errorOf("audio_format")}
      >
        {AUDIO_FORMATS.map((format, index) => (
          <Field
            key={format}
            layout="inline"
            id={index === 0 ? fieldId("audio_format") : `${fieldId("audio_format")}-${format}`}
            label={T.audioFormats[format]}
          >
            {(control) => (
              <input
                {...control}
                type="radio"
                name={fieldId("audio_format")}
                value={format}
                disabled={audioOff}
                checked={values.audio_format === format}
                onChange={() => {
                  update({ ...values, audio_format: format });
                }}
              />
            )}
          </Field>
        ))}
      </FieldGroup>
      <Field
        id={fieldId("audio_stream_index")}
        label={T.audioStreamIndex}
        description={T.audioStreamIndexHint(limits.maxStreamIndex)}
        error={errorOf("audio_stream_index")}
      >
        {(control) => (
          <input
            {...control}
            inputMode="numeric"
            disabled={audioOff}
            value={values.audio_stream_index}
            onChange={(event) => {
              update({ ...values, audio_stream_index: event.currentTarget.value });
            }}
          />
        )}
      </Field>
      <FieldGroup
        id={fieldId("thumbnail_count")}
        legend={T.thumbnailCount}
        description={T.thumbnailCountHint(limits.thumbnailMaxSide)}
        error={errorOf("thumbnail_count")}
      >
        {THUMBNAIL_COUNTS.map((count, index) => (
          <Field
            key={count}
            layout="inline"
            id={
              index === 0
                ? fieldId("thumbnail_count")
                : `${fieldId("thumbnail_count")}-${String(count)}`
            }
            label={T.thumbnailCounts[count]}
          >
            {(control) => (
              <input
                {...control}
                type="radio"
                name={fieldId("thumbnail_count")}
                value={count}
                checked={values.thumbnail_count === count}
                onChange={() => {
                  update({ ...values, thumbnail_count: count });
                }}
              />
            )}
          </Field>
        ))}
      </FieldGroup>
    </>
  );
}
