import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, Download } from "lucide-react";
import { useId, useRef, type ReactElement, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { z } from "zod";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { NotFoundPage } from "../app/NotFoundPage";
import { workbenchPath } from "../app/paths";
import { GoblinIllustration, type GoblinState } from "../art/GoblinIllustration";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { EmptyState } from "../components/feedback/EmptyState";
import { ProblemAlert, type DisplayableFailure } from "../components/feedback/ProblemAlert";
import { buttonClassName } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { StatusBadge } from "../components/ui/StatusBadge";
import { Steps } from "../components/ui/Steps";
import { formatDateTime } from "../format";
import { findRecipe } from "../recipes/registry";
import { TEXT } from "../text";
import type { Run, RunStatus } from "./api";
import type { RunResults as RunResultsData } from "./commands";
import { artifactDownloadPath, getRunResults } from "./commands";
import { ResultsAvailability } from "./ResultsAvailability";
import { useRunPolling } from "./useRunPolling";

const T = TEXT.run;
const runIdSchema = z.uuid();

const SUCCEEDED: ReadonlySet<RunStatus> = new Set(["succeeded", "succeeded_with_warnings"]);
/** Estados em que a fase atual diz algo: há trabalho em curso. */
const WORKING: ReadonlySet<RunStatus> = new Set(["running", "stopping"]);
/** Goblin de cada estado; a cancelada fica sem, porque não é espera, sucesso nem erro. */
const GOBLIN_OF_STATUS: Readonly<Record<RunStatus, GoblinState | null>> = {
  queued: "carregando",
  running: "carregando",
  stopping: "carregando",
  succeeded: "sucesso",
  succeeded_with_warnings: "sucesso",
  failed: "erro",
  canceled: null,
};

/** Rota de uma execução: o id da URL só vira consulta se for um UUID. */
export function RunPage(): ReactElement {
  const parsed = runIdSchema.safeParse(useParams()["runId"]);
  if (!parsed.success) {
    return <NotFoundPage />;
  }
  return <RunView key={parsed.data} runId={parsed.data} />;
}

/**
 * Execução e resultado na mesma rota (2.2): estado por polling que respeita o Retry-After,
 * mudanças anunciadas em `aria-live` e, ao concluir, os downloads.
 */
function RunView({ runId }: { readonly runId: string }): ReactElement {
  const { query, announcement } = useRunPolling(runId);
  const run = query.data;
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
      <div className="flex flex-col gap-2">
        {run !== undefined && <BackToWorkbench recipeId={run.recipe_id} />}
        <FocusHeading>{T.title}</FocusHeading>
      </div>
      <LiveRegion message={announcement} visuallyHidden />
      {run === undefined && query.isPending && <p className="text-fg-muted">{T.loading}</p>}
      {query.error !== null && <ProblemAlert failure={failureOf(query.error)} />}
      {run !== undefined && <RunDetails run={run} />}
      {run !== undefined && SUCCEEDED.has(run.status) && <RunResults runId={run.id} />}
      {run !== undefined && <NewRunLink recipeId={run.recipe_id} />}
    </div>
  );
}

function BackToWorkbench({ recipeId }: { readonly recipeId: string }): ReactElement | null {
  const recipe = findRecipe(recipeId);
  if (recipe === null) {
    return null;
  }
  return (
    <Link
      to={workbenchPath(recipe.id)}
      className="inline-flex min-h-11 items-center gap-1 self-start text-sm font-semibold"
    >
      <ChevronLeft aria-hidden="true" className="size-4" />
      {T.backToWorkbench(recipe.title)}
    </Link>
  );
}

/**
 * Cartão de estado. Os rótulos que só repetiriam o óbvio ficam para o leitor de tela (`sr-only`):
 * o selo já diz o estado e o texto grande diz a fase (RUI cap. 2, "Labels are a last resort").
 */
function RunDetails({ run }: { readonly run: Run }): ReactElement {
  const recipe = findRecipe(run.recipe_id);
  const { phase, processed, total } = run.progress;
  const flavor = flavorOf(run.status);
  const goblin = GOBLIN_OF_STATUS[run.status];
  return (
    <Card>
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        <div className="flex flex-1 flex-col gap-6">
          <dl className="flex flex-wrap items-center justify-between gap-3">
            <Detail label={T.status} hiddenLabel>
              <StatusBadge status={run.status} />
            </Detail>
            <Detail label={T.recipe} hiddenLabel>
              <span className="text-fg-muted">{recipe?.title ?? run.recipe_id}</span>
            </Detail>
          </dl>
          {flavor !== null && <p className="text-fg-muted">{flavor}</p>}
          {phase !== null && (
            <dl>
              <Detail label={T.phase} hiddenLabel>
                <span className="text-lg font-semibold text-fg">{T.phases[phase]}</span>
              </Detail>
            </dl>
          )}
          {phase !== null && WORKING.has(run.status) && <Steps phase={phase} />}
          <dl className="flex flex-col gap-6">
            {total > 0 && (
              <Detail label={T.progress}>
                <span className="flex flex-col gap-2">
                  <span className="tabular-nums">{T.progressValue(processed, total)}</span>
                  <progress aria-label={T.progress} value={processed} max={total} />
                </span>
              </Detail>
            )}
            {run.status === "queued" && (
              <Detail label={T.queueAhead}>
                <span className="tabular-nums">{run.queue_ahead}</span>
              </Detail>
            )}
            {run.error !== null && (
              <Detail label={T.failureReason}>
                <span className="font-semibold text-danger-fg">{run.error.message}</span>
              </Detail>
            )}
            <Detail label={T.attempt}>
              <span className="tabular-nums">{run.attempt}</span>
            </Detail>
          </dl>
        </div>
        {/* Depois do texto: o leitor de tela ouve o estado antes da goblin, e no celular ela fica
            embaixo, sem empurrar o estado para fora da primeira dobra. */}
        {goblin !== null && <GoblinIllustration state={goblin} rotationKey={run.id} />}
      </div>
    </Card>
  );
}

interface DetailProps {
  readonly label: string;
  /** Rótulo só para o leitor de tela, quando o valor já se explica na tela. */
  readonly hiddenLabel?: boolean;
  readonly children: ReactNode;
}

function Detail({ label, hiddenLabel = false, children }: DetailProps): ReactElement {
  return (
    <div className="flex flex-col gap-1">
      <dt className={hiddenLabel ? "sr-only" : "text-sm text-fg-muted"}>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Uma frase leve da voz (D11), só na espera e no sucesso; nunca no erro. */
function flavorOf(status: RunStatus): string | null {
  if (status === "queued") {
    return T.flavor.queued;
  }
  return SUCCEEDED.has(status) ? T.flavor.succeeded : null;
}

function RunResults({ runId }: { readonly runId: string }): ReactElement {
  const client = useApiClient();
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const results = useQuery({
    queryKey: ["runs", runId, "results"] as const,
    queryFn: ({ signal }) => getRunResults(client, runId, signal),
    retry: false,
  });
  return (
    <Card as="section" aria-labelledby={headingId}>
      {/* tabIndex -1: recebe o foco quando Excluir resultados some com ele. */}
      <h2
        id={headingId}
        ref={heading}
        tabIndex={-1}
        className="mb-6 font-display text-xl font-semibold text-fg"
      >
        {T.results.heading}
      </h2>
      {results.isPending && <p className="text-fg-muted">{T.results.loading}</p>}
      {results.error !== null && <ProblemAlert failure={failureOf(results.error)} />}
      {results.data !== undefined && (
        <ResultsAvailability
          runId={runId}
          results={results.data}
          onDeleted={() => heading.current?.focus()}
        >
          <ResultFiles results={results.data} />
        </ResultsAvailability>
      )}
    </Card>
  );
}

/**
 * O pacote com tudo é a ação primária da tela (Baixar); os arquivos avulsos são links (pirâmide de
 * ações, RUI cap. 2). Download sempre por navegação, nunca por fetch (2.2).
 */
function ResultFiles({ results }: { readonly results: RunResultsData }): ReactElement {
  if (!results.published || results.artifacts.length === 0) {
    return (
      <EmptyState title={T.results.empty} headingLevel={3}>
        <p>{T.results.emptyHint}</p>
      </EmptyState>
    );
  }
  const packages = results.artifacts.filter((artifact) => artifact.role === "package");
  const others = results.artifacts.filter((artifact) => artifact.role !== "package");
  return (
    <div className="flex flex-col gap-6">
      {packages.map((artifact) => (
        <a
          key={artifact.artifact_id}
          href={artifactDownloadPath(artifact.artifact_id)}
          download={artifact.file_name}
          className={buttonClassName({ variant: "primary", size: "lg" })}
        >
          <Download aria-hidden="true" className="size-6" />
          {T.results.download(artifact.file_name)}
          {/* Espaço fora do sr-only, que fora do fluxo perde o espaço inicial (HomePage). */}{" "}
          <span className="sr-only">({T.results.roles[artifact.role]})</span>
        </a>
      ))}
      {others.length > 0 && (
        <div className="flex flex-col gap-2">
          {packages.length > 0 && (
            <h3 className="text-sm font-semibold text-fg-muted">{T.results.otherFiles}</h3>
          )}
          <ul className="flex flex-col gap-2">
            {others.map((artifact) => (
              <li key={artifact.artifact_id} className="flex flex-wrap items-baseline gap-x-2">
                <a
                  href={artifactDownloadPath(artifact.artifact_id)}
                  download={artifact.file_name}
                  className="font-mono text-sm"
                >
                  {T.results.download(artifact.file_name)}
                </a>
                <span className="text-sm text-fg-muted">({T.results.roles[artifact.role]})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {results.expires_at !== null && (
        <p className="text-sm text-fg-muted">
          {T.results.availableUntil(formatDateTime(results.expires_at))}
        </p>
      )}
    </div>
  );
}

function NewRunLink({ recipeId }: { readonly recipeId: string }): ReactElement | null {
  const recipe = findRecipe(recipeId);
  if (recipe === null) {
    return null;
  }
  return (
    <p>
      <Link
        to={workbenchPath(recipe.id)}
        className="inline-flex min-h-11 items-center font-semibold"
      >
        {T.newRun}
      </Link>
    </p>
  );
}

function failureOf(error: Error): DisplayableFailure {
  return error instanceof ApiError ? error.failure : { kind: "internal" };
}
