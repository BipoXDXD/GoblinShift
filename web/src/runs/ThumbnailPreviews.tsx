import { useEffect, useId, useState, type ReactElement } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { TEXT } from "../text";
import {
  browserPreviewServices,
  loadPreviews,
  type PreviewServices,
  type PreviewState,
  type ThumbnailArtifact,
} from "./thumbnails";

const T = TEXT.run.results.thumbnails;

interface ThumbnailPreviewsProps {
  /** Já filtradas por `thumbnailArtifacts`. */
  readonly thumbnails: readonly ThumbnailArtifact[];
  /** Só os testes trocam; na tela, os serviços do navegador sobre o cliente da API. */
  readonly services?: PreviewServices;
}

/** Galeria das miniaturas geradas; o motivo do fetch aqui está em thumbnails.ts. */
export function ThumbnailPreviews({ thumbnails, services }: ThumbnailPreviewsProps): ReactElement {
  const client = useApiClient();
  const headingId = useId();
  const states = usePreviews(thumbnails, services ?? browserPreviewServices(client));
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h3 id={headingId} className="text-sm font-semibold text-fg-muted">
        {T.heading}
      </h3>
      <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {thumbnails.map((thumbnail) => (
          <li key={thumbnail.artifact_id}>
            <Preview
              name={thumbnail.file_name}
              state={states.get(thumbnail.artifact_id) ?? LOADING}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

const LOADING: PreviewState = { status: "loading" };

function Preview({ name, state }: { name: string; state: PreviewState }): ReactElement {
  const label = baseName(name);
  return (
    <figure className="flex flex-col gap-2">
      <div className="flex aspect-video items-center justify-center overflow-hidden rounded-md bg-surface-sunken">
        {state.status === "ready" ? (
          <img src={state.url} alt={T.alt(label)} className="size-full object-contain" />
        ) : (
          <p className="px-2 text-center text-sm text-fg-muted">
            {state.status === "loading" ? T.loading : T.failed}
          </p>
        )}
      </div>
      <figcaption className="font-mono text-xs break-all text-fg-muted">{label}</figcaption>
    </figure>
  );
}

function baseName(fileName: string): string {
  return fileName.slice(fileName.lastIndexOf("/") + 1);
}

/** Estado de cada miniatura; as URLs de Blob são revogadas quando a lista muda ou a tela sai. */
function usePreviews(
  thumbnails: readonly ThumbnailArtifact[],
  services: PreviewServices,
): ReadonlyMap<string, PreviewState> {
  const [states, setStates] = useState<ReadonlyMap<string, PreviewState>>(new Map());
  const ids = thumbnails.map((thumbnail) => thumbnail.artifact_id).join(",");
  // `services` muda a cada render; a busca só recomeça quando a lista de miniaturas muda.
  const [stableServices] = useState(services);

  useEffect(() => {
    const controller = new AbortController();
    const created: string[] = [];
    void loadPreviews({
      artifactIds: ids === "" ? [] : ids.split(","),
      services: stableServices,
      onResult: (artifactId, state) => {
        if (state.status === "ready") {
          created.push(state.url);
        }
        setStates((current) => new Map(current).set(artifactId, state));
      },
      signal: controller.signal,
    });
    return () => {
      controller.abort();
      setStates(new Map());
      for (const url of created) {
        URL.revokeObjectURL(url);
      }
    };
  }, [ids, stableServices]);

  return states;
}
