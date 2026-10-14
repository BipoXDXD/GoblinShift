import { useQuery } from "@tanstack/react-query";
import { useId, type ReactElement } from "react";
import { Link, useParams } from "react-router";
import { z } from "zod";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { NotFoundPage } from "../app/NotFoundPage";
import { workbenchPath } from "../app/paths";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { EmptyState } from "../components/feedback/EmptyState";
import { ProblemAlert, type DisplayableFailure } from "../components/feedback/ProblemAlert";
import { formatDateTime } from "../format";
import { findRecipe } from "../recipes/registry";
import { TEXT } from "../text";
import type { Run, RunStatus } from "./api";
import { artifactDownloadPath, getRunResults } from "./commands";
import { useRunPolling } from "./useRunPolling";

const T = TEXT.run;
const runIdSchema = z.uuid();

const SUCCEEDED: ReadonlySet<RunStatus> = new Set(["succeeded", "succeeded_with_warnings"]);

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
    <main>
      <FocusHeading>{T.title}</FocusHeading>
      <LiveRegion message={announcement} visuallyHidden />
      {run === undefined && query.isPending && <p>{T.loading}</p>}
      {query.error !== null && <ProblemAlert failure={failureOf(query.error)} />}
      {run !== undefined && <RunDetails run={run} />}
      {run !== undefined && SUCCEEDED.has(run.status) && <RunResults runId={run.id} />}
      {run !== undefined && <NewRunLink recipeId={run.recipe_id} />}
    </main>
  );
}

function RunDetails({ run }: { readonly run: Run }): ReactElement {
  const recipe = findRecipe(run.recipe_id);
  const { phase, processed, total } = run.progress;
  return (
    <dl>
      <dt>{T.recipe}</dt>
      <dd>{recipe?.title ?? run.recipe_id}</dd>
      <dt>{T.status}</dt>
      <dd>{T.statuses[run.status]}</dd>
      {phase !== null && (
        <>
          <dt>{T.phase}</dt>
          <dd>{T.phases[phase]}</dd>
        </>
      )}
      <dt>{T.attempt}</dt>
      <dd>{run.attempt}</dd>
      {total > 0 && (
        <>
          <dt>{T.progress}</dt>
          <dd>{T.progressValue(processed, total)}</dd>
        </>
      )}
      {run.status === "queued" && (
        <>
          <dt>{T.queueAhead}</dt>
          <dd>{run.queue_ahead}</dd>
        </>
      )}
      {run.error !== null && (
        <>
          <dt>{T.failureReason}</dt>
          <dd>{run.error.message}</dd>
        </>
      )}
    </dl>
  );
}

function RunResults({ runId }: { readonly runId: string }): ReactElement {
  const client = useApiClient();
  const headingId = useId();
  const results = useQuery({
    queryKey: ["runs", runId, "results"] as const,
    queryFn: ({ signal }) => getRunResults(client, runId, signal),
    retry: false,
  });
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId}>{T.results.heading}</h2>
      {results.isPending && <p>{T.results.loading}</p>}
      {results.error !== null && <ProblemAlert failure={failureOf(results.error)} />}
      {results.data !== undefined &&
        (results.data.published && results.data.artifacts.length > 0 ? (
          <>
            <ul>
              {results.data.artifacts.map((artifact) => (
                <li key={artifact.artifact_id}>
                  <a
                    href={artifactDownloadPath(artifact.artifact_id)}
                    download={artifact.file_name}
                  >
                    {T.results.download(artifact.file_name)}
                  </a>{" "}
                  ({T.results.roles[artifact.role]})
                </li>
              ))}
            </ul>
            {results.data.expires_at !== null && (
              <p>{T.results.availableUntil(formatDateTime(results.data.expires_at))}</p>
            )}
          </>
        ) : (
          <EmptyState title={T.results.empty} headingLevel={3}>
            <p>{T.results.emptyHint}</p>
          </EmptyState>
        ))}
    </section>
  );
}

function NewRunLink({ recipeId }: { readonly recipeId: string }): ReactElement | null {
  const recipe = findRecipe(recipeId);
  if (recipe === null) {
    return null;
  }
  return (
    <p>
      <Link to={workbenchPath(recipe.id)}>{T.newRun}</Link>
    </p>
  );
}

function failureOf(error: Error): DisplayableFailure {
  return error instanceof ApiError ? error.failure : { kind: "internal" };
}
