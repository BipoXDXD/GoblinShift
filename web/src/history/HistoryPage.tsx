import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SubmitEvent, type ReactElement } from "react";
import { Link, useSearchParams } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { PATHS } from "../app/paths";
import { GoblinIllustration } from "../art/GoblinIllustration";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { EmptyState } from "../components/feedback/EmptyState";
import { ProblemAlert, type DisplayableFailure } from "../components/feedback/ProblemAlert";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button, buttonClassName } from "../components/ui/Button";
import { RECIPE_LIST } from "../recipes/registry";
import { RUN_STATUSES } from "../runs/api";
import { TEXT } from "../text";
import { listRuns, type RunList, type RunListQuery } from "./api";
import {
  filtersToSearchParams,
  hasActiveFilters,
  parseHistoryFilters,
  PERIODS,
  toRunListQuery,
  type HistoryFilters,
} from "./filters";
import { RunHistoryExtras } from "./RunHistoryExtras";
import { RunList as RunRows } from "./RunList";

const T = TEXT.history;

interface HistoryPageProps {
  readonly now: () => number;
}

/**
 * Histórico (2.2): as execuções da conta, das mais novas para as mais antigas, com filtros por
 * receita, estado e período guardados na URL e "Carregar mais" pelo cursor da API (keyset).
 */
export function HistoryPage({ now }: HistoryPageProps): ReactElement {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = parseHistoryFilters(searchParams);
  // O período conta a partir da abertura da tela: o cursor da API vale só para os mesmos filtros,
  // então o início do período não pode andar enquanto a lista está aberta.
  const [anchorMs] = useState(now);
  return (
    <div className="flex flex-col gap-8">
      <FocusHeading>{T.title}</FocusHeading>
      <HistoryResults
        query={toRunListQuery(filters, anchorMs)}
        nowMs={anchorMs}
        filters={filters}
        onApplyFilters={(next) => {
          setSearchParams(filtersToSearchParams(next));
        }}
      />
    </div>
  );
}

interface HistoryResultsProps {
  readonly query: RunListQuery;
  /** Relógio da abertura da tela, para dizer se os resultados ainda valem. */
  readonly nowMs: number;
  readonly filters: HistoryFilters;
  readonly onApplyFilters: (filters: HistoryFilters) => void;
}

/** Sem cursor: a primeira página. */
const FIRST_PAGE: string | null = null;

function HistoryResults({
  query,
  nowMs,
  filters,
  onApplyFilters,
}: HistoryResultsProps): ReactElement {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const [announcement, setAnnouncement] = useState<string | null>(null);
  const queryKey = ["runs", "history", query] as const;
  const runs = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam, signal }) =>
      listRuns(client, pageParam === null ? query : { ...query, cursor: pageParam }, signal),
    initialPageParam: FIRST_PAGE,
    getNextPageParam: (last: RunList) => last.next_cursor,
  });

  const loadMore = async (): Promise<void> => {
    if (runs.isFetchingNextPage) {
      return;
    }
    setAnnouncement(T.loadingMore);
    const result = await runs.fetchNextPage();
    if (result.isFetchNextPageError) {
      if (isCursorRejected(result.error)) {
        // O cursor venceu (ou os filtros mudaram por baixo): recomeça do início em vez de travar.
        await queryClient.resetQueries({ queryKey, exact: true });
        setAnnouncement(T.restarted);
      } else {
        setAnnouncement(null);
      }
      return;
    }
    setAnnouncement(T.loadedMore(result.data?.pages.at(-1)?.data.length ?? 0));
  };

  const active = hasActiveFilters(filters);
  const items = runs.data?.pages.flatMap((entry) => entry.data) ?? [];
  // Sem nada para filtrar, os filtros só atrapalham o estado vazio.
  const showFilters = active || items.length > 0;
  return (
    <>
      {showFilters && (
        <FiltersForm
          key={filtersToSearchParams(filters).toString()}
          filters={filters}
          onApply={onApplyFilters}
        />
      )}
      {runs.data === undefined ? (
        <FirstPagePending error={runs.error} />
      ) : items.length === 0 ? (
        <EmptyResults filtered={active} />
      ) : (
        <div className="flex flex-col gap-4">
          <RunRows
            runs={items}
            renderExtras={(run) => <RunHistoryExtras run={run} nowMs={nowMs} />}
          />
          {runs.isFetchNextPageError && !isCursorRejected(runs.error) && (
            <ProblemAlert failure={failureOf(runs.error)} />
          )}
          {runs.hasNextPage && (
            <Button
              type="button"
              variant="secondary"
              aria-disabled={runs.isFetchingNextPage}
              onClick={() => {
                void loadMore();
              }}
            >
              {T.loadMore}
            </Button>
          )}
        </div>
      )}
      <LiveRegion message={announcement} visuallyHidden />
    </>
  );
}

function FirstPagePending({ error }: { readonly error: Error | null }): ReactElement {
  return error === null ? (
    <p className="text-fg-muted">{T.loading}</p>
  ) : (
    <FailureNotice error={error} />
  );
}

function EmptyResults({ filtered }: { readonly filtered: boolean }): ReactElement {
  if (filtered) {
    return (
      <EmptyState title={T.noMatches}>
        <p>{T.noMatchesHint}</p>
      </EmptyState>
    );
  }
  return (
    <EmptyState
      title={T.empty}
      illustration={<GoblinIllustration state="vazio" rotationKey="historico" />}
    >
      <p>{T.emptyHint}</p>
      <Link to={PATHS.home} className="inline-flex min-h-11 items-center font-semibold">
        {T.goToWorkshop}
      </Link>
    </EmptyState>
  );
}

interface FiltersFormProps {
  readonly filters: HistoryFilters;
  readonly onApply: (filters: HistoryFilters) => void;
}

/**
 * Filtros num `<details>`, aberto quando há filtro ativo. O formulário guarda a escolha até
 * "Aplicar"; a URL é a fonte da verdade, e a `key` recria o formulário quando ela muda.
 */
function FiltersForm({ filters, onApply }: FiltersFormProps): ReactElement {
  const [draft, setDraft] = useState(filters);
  const ids = { recipe: useId(), status: useId(), period: useId() };
  const onSubmit = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    onApply(draft);
  };
  return (
    <details open={hasActiveFilters(filters)} className="rounded-lg bg-surface p-4 shadow-card">
      <summary className="inline-flex min-h-11 cursor-pointer items-center font-semibold">
        {T.filters}
      </summary>
      <form onSubmit={onSubmit} className="flex flex-col gap-6 pt-4">
        <div className="grid gap-4 md:grid-cols-3">
          <FilterSelect
            id={ids.recipe}
            label={T.recipe}
            value={draft.recipe ?? ""}
            options={[
              { value: "", label: T.anyRecipe },
              ...RECIPE_LIST.map((recipe) => ({ value: recipe.id, label: recipe.title })),
            ]}
            onChange={(value) => {
              setDraft({ ...draft, recipe: RECIPE_LIST.find((r) => r.id === value)?.id ?? null });
            }}
          />
          <FilterSelect
            id={ids.status}
            label={T.status}
            value={draft.status ?? ""}
            options={[
              { value: "", label: T.anyStatus },
              ...RUN_STATUSES.map((status) => ({
                value: status,
                label: TEXT.run.statuses[status],
              })),
            ]}
            onChange={(value) => {
              setDraft({ ...draft, status: RUN_STATUSES.find((s) => s === value) ?? null });
            }}
          />
          <FilterSelect
            id={ids.period}
            label={T.period}
            value={draft.period ?? ""}
            options={[
              { value: "", label: T.anyPeriod },
              ...PERIODS.map((period) => ({ value: period, label: T.periods[period] })),
            ]}
            onChange={(value) => {
              setDraft({ ...draft, period: PERIODS.find((p) => p === value) ?? null });
            }}
          />
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <Button type="submit" variant="secondary">
            {T.apply}
          </Button>
          {hasActiveFilters(filters) && (
            <Link to={PATHS.history} className={buttonClassName({ variant: "tertiary" })}>
              {T.clear}
            </Link>
          )}
        </div>
      </form>
    </details>
  );
}

interface FilterSelectProps {
  readonly id: string;
  readonly label: string;
  readonly value: string;
  readonly options: readonly { readonly value: string; readonly label: string }[];
  readonly onChange: (value: string) => void;
}

function FilterSelect({ id, label, value, options, onChange }: FilterSelectProps): ReactElement {
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-semibold text-fg">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => {
          onChange(event.currentTarget.value);
        }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function failureOf(error: Error | null): DisplayableFailure {
  return error instanceof ApiError ? error.failure : { kind: "internal" };
}

/** 400 na página seguinte: o cursor é inválido, venceu ou foi emitido para outros filtros. */
function isCursorRejected(error: Error | null): boolean {
  const failure = failureOf(error);
  return failure.kind === "problem" && failure.problem.code === "BAD_REQUEST";
}
