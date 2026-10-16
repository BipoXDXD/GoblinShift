import { useQuery } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useId, useState, type ReactElement, type ReactNode } from "react";

import { useApiClient } from "../../api/ApiClientContext";
import { ApiError } from "../../api/response";
import { TEXT } from "../../text";
import type { ResultReference } from "../commands";
import { artifactPreviewPath, getCsvPreview, getPageMapPreview, type PageMap } from "./api";
import type { CsvSample } from "./csvSample";

const T = TEXT.run.results.preview;

type PreviewKind = "image" | "table" | "pages";

/** Formato pelo nome que a receita deu ao preview (ADR 037): JPEG, amostra CSV ou mapa JSON. */
const KIND_OF_EXTENSION: Readonly<Record<string, PreviewKind>> = {
  ".jpg": "image",
  ".csv": "table",
  ".json": "pages",
};

/** Lado do quadrado da imagem, em px: o JPEG do Image Forge cabe em 512, mostrado reduzido. */
const IMAGE_BOX_PX = 160;

interface ResultPreviewProps {
  /** A saída que o preview mostra; a rota de preview recebe o id dela. */
  readonly source: ResultReference;
  readonly preview: ResultReference;
}

/**
 * O preview de uma saída, ao lado do download dela (2.2): imagem reduzida, amostra CSV em tabela
 * ou mapa de páginas do PDF. Formato que a interface não conhece não aparece (tolerant reader).
 */
export function ResultPreview({ source, preview }: ResultPreviewProps): ReactElement | null {
  const kind = previewKindOf(preview.logical_name);
  if (kind === null) {
    return null;
  }
  const unavailable = unavailabilityOf(source, preview);
  if (unavailable !== null) {
    return <PreviewNote>{T.unavailable[unavailable]}</PreviewNote>;
  }
  switch (kind) {
    case "image":
      return <ImagePreview source={source} />;
    case "table":
      return <TablePreview source={source} />;
    case "pages":
      return <PageMapPreview source={source} />;
  }
}

function previewKindOf(logicalName: string): PreviewKind | null {
  const dot = logicalName.lastIndexOf(".");
  const extension = dot === -1 ? "" : logicalName.slice(dot).toLowerCase();
  return KIND_OF_EXTENSION[extension] ?? null;
}

/** O preview só existe enquanto a saída existe; excluído pesa mais que vencido. */
function unavailabilityOf(
  source: ResultReference,
  preview: ResultReference,
): "expired" | "deleted" | null {
  const states = [source.availability, preview.availability];
  if (states.includes("deleted")) {
    return "deleted";
  }
  return states.includes("expired") ? "expired" : null;
}

type ImageState = "loading" | "loaded" | "failed";

/**
 * Imagem por `<img>` direto na rota da API: mesma origem, que a CSP da SPA já libera em
 * `img-src 'self'`, sem Blob nem `URL.createObjectURL`. Quadrado fixo com `object-contain` para a
 * lista não pular quando a imagem chega (CLS) e para nada ser cortado.
 */
function ImagePreview({ source }: { readonly source: ResultReference }): ReactElement {
  const [state, setState] = useState<ImageState>("loading");
  if (state === "failed") {
    return <PreviewFailureNote message={T.failed} />;
  }
  return (
    <div className="flex flex-col gap-2">
      <img
        src={artifactPreviewPath(source.artifact_id)}
        alt={T.imageAlt(source.file_name)}
        width={IMAGE_BOX_PX}
        height={IMAGE_BOX_PX}
        loading="lazy"
        decoding="async"
        className="size-40 rounded-lg bg-surface-sunken object-contain shadow-sunken"
        onLoad={() => {
          setState("loaded");
        }}
        onError={() => {
          setState("failed");
        }}
      />
      {state === "loading" && <PreviewNote>{T.loading}</PreviewNote>}
    </div>
  );
}

function TablePreview({ source }: { readonly source: ResultReference }): ReactElement {
  const client = useApiClient();
  const sample = useQuery({
    queryKey: ["artifacts", source.artifact_id, "preview", "table"] as const,
    queryFn: ({ signal }) => getCsvPreview(client, source.artifact_id, signal),
    retry: false,
    // O preview publicado não muda; vale até a pessoa sair da tela.
    staleTime: Infinity,
  });
  if (sample.isPending) {
    return <PreviewNote>{T.loading}</PreviewNote>;
  }
  if (sample.isError) {
    return <PreviewFailure error={sample.error} />;
  }
  return <SampleTable fileName={source.file_name} sample={sample.data} />;
}

/** Cada valor entra como texto do React, que o escapa: fórmula e HTML aparecem como escritos. */
function SampleTable({
  fileName,
  sample,
}: {
  readonly fileName: string;
  readonly sample: CsvSample;
}): ReactElement {
  const captionId = useId();
  return (
    // Tabela larga rola dentro da própria caixa, não a página (WCAG 1.4.10); com foco, rola pelo
    // teclado (regra scrollable-region-focusable do axe), como em LegalDocument. O foco anuncia a
    // região pelo nome da legenda.
    <div
      role="region"
      aria-labelledby={captionId}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- caixa rolável precisa de foco para o teclado
      tabIndex={0}
      className="overflow-x-auto rounded-lg bg-surface-sunken p-3 shadow-sunken"
    >
      <table className="w-full border-collapse text-left text-sm">
        <caption id={captionId} className="mb-2 text-left text-fg-muted">
          {T.tableCaption(fileName, sample.rows.length)}
        </caption>
        <thead>
          <tr>
            {sample.header.map((name, index) => (
              <th
                key={index}
                scope="col"
                className="border-b-2 border-line px-3 py-2 font-semibold whitespace-nowrap"
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sample.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((value, index) => (
                <td
                  key={index}
                  className="max-w-xs border-b border-line-soft px-3 py-2 align-top break-words tabular-nums"
                >
                  {value}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PageMapPreview({ source }: { readonly source: ResultReference }): ReactElement {
  const client = useApiClient();
  const pageMap = useQuery({
    queryKey: ["artifacts", source.artifact_id, "preview", "pages"] as const,
    queryFn: ({ signal }) => getPageMapPreview(client, source.artifact_id, signal),
    retry: false,
    staleTime: Infinity,
  });
  if (pageMap.isPending) {
    return <PreviewNote>{T.loading}</PreviewNote>;
  }
  if (pageMap.isError) {
    return <PreviewFailure error={pageMap.error} />;
  }
  return <PageList fileName={source.file_name} pageMap={pageMap.data} />;
}

function PageList({
  fileName,
  pageMap,
}: {
  readonly fileName: string;
  readonly pageMap: PageMap;
}): ReactElement {
  const captionId = useId();
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-surface-sunken p-3 shadow-sunken">
      <p id={captionId} className="text-sm text-fg-muted">
        {T.pagesCaption(fileName, pageMap.pages.length)}
      </p>
      {/* Até 400 páginas: a lista rola dentro da caixa, com foco para o teclado e o nome da
          legenda ao receber o foco. */}
      <div
        role="region"
        aria-labelledby={captionId}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- caixa rolável precisa de foco para o teclado
        tabIndex={0}
        className="max-h-48 overflow-y-auto rounded-lg"
      >
        <ol
          aria-labelledby={captionId}
          className="grid list-inside list-decimal gap-x-6 gap-y-1 text-sm sm:grid-cols-2"
        >
          {pageMap.pages.map((page, index) => (
            <li key={index} className="tabular-nums">
              {T.page(page.page_number, page.input_position)}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function PreviewFailure({ error }: { readonly error: Error }): ReactElement {
  const gone =
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.problem.code === "GONE";
  return <PreviewFailureNote message={gone ? T.gone : T.failed} />;
}

function PreviewFailureNote({ message }: { readonly message: string }): ReactElement {
  return (
    <p className="flex items-center gap-2 text-sm text-fg-muted">
      <CircleAlert aria-hidden="true" className="size-4 flex-none" />
      {message}
    </p>
  );
}

function PreviewNote({ children }: { readonly children: ReactNode }): ReactElement {
  return <p className="text-sm text-fg-muted">{children}</p>;
}
