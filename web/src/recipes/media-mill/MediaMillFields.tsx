import { useState, type ReactElement } from "react";

import { Field } from "../../components/a11y/Field";
import { FieldGroup } from "../../components/a11y/FieldGroup";
import { TEXT } from "../../text";
import type { RecipeFieldsProps } from "../types";
import {
  AUDIO_FORMATS,
  fieldId,
  isLossy,
  parseMediaMill,
  THUMBNAIL_COUNTS,
  type MediaMillFormValues,
  type MediaMillSettings,
} from "./schema";

const T = TEXT.mediaMill;
const HZ_PER_KHZ = 1000;

/**
 * Formulário do Media Mill v2, escrito à mão (2.2), com defaults, limites e bitrates do catálogo.
 * Formato, faixa e bitrate ficam desabilitados, mas visíveis, sem a extração, e o bitrate também
 * em WAV e FLAC, com a dica do motivo: a pessoa vê o que existe.
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
  const lossyFormat = isLossy(values.audio_format) ? values.audio_format : null;
  const lossy = lossyFormat !== null;
  // Em WAV e FLAC o comando leva o default; o seletor desabilitado mostra o mesmo valor.
  const shownBitrate = lossy ? values.audio_bitrate_kbps : settings.defaults.audio_bitrate_kbps;
  const bitrates = lossyFormat === null ? [shownBitrate] : settings.bitratesByFormat[lossyFormat];
  const formatHint = [
    lossy ? T.lossyFormatHint : T.audioFormatHint,
    ...(values.audio_format === "opus" ? [T.opusHint] : []),
    ...(audioOff ? [T.audioDisabledHint] : []),
  ].join(" ");
  const bitrateHint = audioOff
    ? T.audioBitrateDisabledHint
    : lossy
      ? undefined
      : T.audioBitrateLosslessHint;

  /** Troca o formato e leva o bitrate ao default se a lista nova não tem o atual. */
  const chooseFormat = (format: MediaMillFormValues["audio_format"]): void => {
    const kept = isLossy(format)
      ? settings.bitratesByFormat[format].includes(values.audio_bitrate_kbps)
      : true;
    update({
      ...values,
      audio_format: format,
      audio_bitrate_kbps: kept ? values.audio_bitrate_kbps : settings.defaults.audio_bitrate_kbps,
    });
  };
  const trimField = (field: "trim_start" | "trim_end", label: string, hint: string) => (
    <Field id={fieldId(field)} label={label} description={hint} error={errorOf(field)}>
      {(control) => (
        // Sem inputMode: o teclado numérico do celular não tem os dois-pontos de `m:ss`.
        <input
          {...control}
          value={values[field]}
          onChange={(event) => {
            update({ ...values, [field]: event.currentTarget.value });
          }}
        />
      )}
    </Field>
  );

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
        description={formatHint}
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
                  chooseFormat(format);
                }}
              />
            )}
          </Field>
        ))}
      </FieldGroup>
      <Field
        id={fieldId("audio_bitrate_kbps")}
        label={T.audioBitrate}
        {...(bitrateHint === undefined ? {} : { description: bitrateHint })}
        error={errorOf("audio_bitrate_kbps")}
      >
        {(control) => (
          <select
            {...control}
            disabled={audioOff || !lossy}
            value={shownBitrate}
            onChange={(event) => {
              const chosen = bitrates.find(
                (bitrate) => String(bitrate) === event.currentTarget.value,
              );
              if (chosen !== undefined) {
                update({ ...values, audio_bitrate_kbps: chosen });
              }
            }}
          >
            {bitrates.map((bitrate) => (
              <option key={bitrate} value={bitrate}>
                {bitrate}
              </option>
            ))}
          </select>
        )}
      </Field>
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
      {trimField("trim_start", T.trimStart, T.trimStartHint)}
      {trimField("trim_end", T.trimEnd, T.trimEndHint)}
    </>
  );
}
