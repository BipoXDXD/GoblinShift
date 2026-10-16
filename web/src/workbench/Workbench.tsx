import { useMutation } from "@tanstack/react-query";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type SubmitEvent,
} from "react";
import { ChevronLeft } from "lucide-react";
import { Link, useNavigate } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { useIdempotencyKey } from "../api/idempotency";
import { ApiError } from "../api/response";
import type { RunCommand } from "../api/types";
import { PATHS, runPath } from "../app/paths";
import { GoblinIllustration } from "../art/GoblinIllustration";
import { ErrorSummary, type FormIssue } from "../components/a11y/ErrorSummary";
import { FileDropzone } from "../components/a11y/FileDropzone";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { moveItem, ReorderableList } from "../components/a11y/ReorderableList";
import { EmptyState } from "../components/feedback/EmptyState";
import { ProblemAlert, type DisplayableFailure } from "../components/feedback/ProblemAlert";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { formatBytes } from "../format";
import type { RecipeForm, RecipeModule } from "../recipes/registry";
import type { RecipeDraft, RecipeId, RecipeInput } from "../recipes/types";
import { createRun } from "../runs/commands";
import { TEXT } from "../text";
import { useUploadBatch, type UploadEntry, type UploadServices } from "../upload/useUploadBatch";
import { orderEntries, runInputs, uploadAnnouncement, type RunInputs } from "./inputs";
import { DeletionFailures } from "./DeletionFailures";
import { UploadEntryView } from "./UploadEntryView";

const FILES_FIELD_ID = "bancada-arquivos";

type PendingRemoval =
  | { readonly kind: "one"; readonly localId: string; readonly name: string }
  | { readonly kind: "all"; readonly count: number };

interface WorkbenchProps {
  readonly recipe: RecipeModule;
  /** Formulário com os defaults e limites da versão no catálogo. */
  readonly form: RecipeForm;
  readonly uploadServices: UploadServices;
}

interface Submission {
  readonly command: RunCommand;
  readonly idempotencyKey: string;
}

/**
 * Bancada de uma receita (2.2): escolher e enviar arquivos, ordenar as entradas, preencher os
 * parâmetros e executar. Os erros só aparecem depois da primeira tentativa de executar; a
 * `Idempotency-Key` acompanha o comando, então repetir o mesmo envio não cria outra execução.
 */
export function Workbench({ recipe, form, uploadServices }: WorkbenchProps): ReactElement {
  const client = useApiClient();
  const navigate = useNavigate();
  const batch = useUploadBatch(uploadServices);
  const [order, setOrder] = useState<readonly string[]>([]);
  const [draft, setDraft] = useState<RecipeDraft<RecipeId>>(form.initialDraft);
  const [attempt, setAttempt] = useState(0);
  const [pendingRemoval, setPendingRemoval] = useState<PendingRemoval | null>(null);
  const focusFilesAfterRemoval = useRef(false);
  const filesHeadingId = useId();
  const parametersHeadingId = useId();

  const entries = orderEntries(batch.entries, order);
  const inputs = runInputs(entries, form.inputLimits);
  const readyInputs = useStableInputs(entries);
  const command = commandFrom(draft, inputs);
  const intent = command === null ? "" : JSON.stringify(command);
  const idempotency = useIdempotencyKey(intent);
  const announcement = useUploadAnnouncement(batch.entries);

  const mutation = useMutation({
    mutationFn: (submission: Submission) => createRun(client, submission),
  });
  const failure = mutation.error === null ? null : failureOf(mutation.error);
  // Erros por campo do servidor valem para o comando enviado; mudou o comando, eles saem de cena.
  const submittedIntent =
    mutation.variables === undefined ? null : JSON.stringify(mutation.variables.command);
  const serverIssues =
    failure !== null && submittedIntent === intent ? fieldIssuesOf(failure, recipe) : null;

  const issues = [...(attempt > 0 ? clientIssues(inputs, draft) : []), ...(serverIssues ?? [])];
  const fieldErrors = new Map(
    issues.flatMap((issue) => (issue.fieldId === null ? [] : [[issue.fieldId, issue.message]])),
  );

  // Remover pede confirmação (decisão do usuário, 2026-10-15). O alvo é o localId guardado ao
  // abrir, nunca a posição: confirmar duas vezes não alcança outro arquivo.
  const askRemoval = (localId: string): void => {
    const entry = batch.entries.find((candidate) => candidate.localId === localId);
    if (entry !== undefined) {
      setPendingRemoval({ kind: "one", localId, name: entry.file.name });
    }
  };

  const confirmRemoval = (): void => {
    if (pendingRemoval === null) {
      return;
    }
    if (pendingRemoval.kind === "one") {
      batch.remove(pendingRemoval.localId);
    } else {
      batch.removeAll();
    }
    focusFilesAfterRemoval.current = true;
    setPendingRemoval(null);
  };

  // O botão Remover some com o item: o foco vai para a escolha de arquivos, e não para o body. Só
  // depois de o diálogo fechar (o efeito do filho roda antes deste), porque com o modal aberto o
  // resto da página está inerte e o focus() não pega.
  useEffect(() => {
    if (pendingRemoval === null && focusFilesAfterRemoval.current) {
      focusFilesAfterRemoval.current = false;
      document.getElementById(FILES_FIELD_ID)?.focus();
    }
  }, [pendingRemoval]);

  const onSubmit = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (mutation.isPending) {
      return;
    }
    setAttempt((current) => current + 1);
    if (command === null) {
      return;
    }
    mutation.mutate(
      { command, idempotencyKey: idempotency.key },
      {
        onSuccess: (run) => {
          idempotency.rotate();
          void navigate(runPath(run.id));
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-8">
      <WorkbenchHeader title={TEXT.workbench.title(recipe.title)} />
      <form noValidate onSubmit={onSubmit} className="flex flex-col gap-8">
        <ErrorSummary issues={issues} attempt={attempt} />
        {/* Duas colunas a partir de 1024 px (D9): arquivos fluida, parâmetros com largura fixa. A
            ordem do DOM é a da leitura e a do Tab: arquivos, parâmetros, Executar. */}
        <div className="flex flex-col gap-8 lg:flex-row lg:items-start">
          <section aria-labelledby={filesHeadingId} className="flex min-w-0 flex-1 flex-col gap-4">
            <h2 id={filesHeadingId} className={SECTION_LABEL}>
              {TEXT.workbench.filesHeading}
            </h2>
            <FileDropzone
              id={FILES_FIELD_ID}
              label={TEXT.workbench.chooseFiles}
              description={`${TEXT.workbench.chooseFilesHint} ${limitsHint(form)}`}
              {...(recipe.accept === null ? {} : { accept: recipe.accept })}
              error={fieldErrors.get(FILES_FIELD_ID) ?? null}
              chosenCount={entries.length}
              onFiles={batch.add}
            />
            {entries.length === 0 ? (
              <EmptyState
                title={TEXT.workbench.noFilesYet}
                headingLevel={3}
                illustration={<GoblinIllustration state="vazio" rotationKey={recipe.id} />}
              >
                <p>{TEXT.workbench.noFilesYetHint}</p>
              </EmptyState>
            ) : (
              <>
                <ReorderableList
                  label={TEXT.workbench.orderLabel}
                  items={entries}
                  getKey={(entry) => entry.localId}
                  getName={(entry) => entry.file.name}
                  numbered={recipe.numberedInputs === true}
                  renderItem={(entry) => (
                    <UploadEntryView entry={entry} onRetry={batch.retry} onRemove={askRemoval} />
                  )}
                  onMove={(move) => {
                    setOrder(moveItem(entries.map(localIdOf), move));
                  }}
                />
                {entries.length >= 2 && (
                  // O Button não aceita className: o wrapper evita que ele estique na coluna.
                  <div className="self-start">
                    <Button
                      type="button"
                      variant="tertiary"
                      onClick={() => {
                        // A contagem fica guardada na abertura (Foco da revisão, item 3).
                        setPendingRemoval({ kind: "all", count: entries.length });
                      }}
                    >
                      {TEXT.workbench.removal.removeAll}
                    </Button>
                  </div>
                )}
              </>
            )}
            <DeletionFailures failures={batch.failedDeletions} onRetry={batch.retryDeletions} />
            <LiveRegion message={announcement} visuallyHidden />
          </section>
          <div className="flex flex-col gap-6 lg:w-112 lg:flex-none">
            <Card as="section" aria-labelledby={parametersHeadingId}>
              <div className="flex flex-col gap-6">
                <h2 id={parametersHeadingId} className={SECTION_LABEL}>
                  {TEXT.workbench.parametersHeading}
                </h2>
                {form.renderFields({
                  errors: fieldErrors,
                  onDraftChange: setDraft,
                  inputs: readyInputs,
                })}
              </div>
            </Card>
            {failure !== null && serverIssues === null && <ProblemAlert failure={failure} />}
            <Button
              type="submit"
              variant="primary"
              size="lg"
              fullWidth
              aria-disabled={mutation.isPending}
            >
              {TEXT.workbench.run}
            </Button>
          </div>
        </div>
        <LiveRegion
          message={mutation.isPending ? TEXT.workbench.creatingRun : null}
          visuallyHidden
        />
        <ConfirmDialog
          open={pendingRemoval !== null}
          title={removalTitle(pendingRemoval)}
          confirmLabel={
            pendingRemoval?.kind === "all"
              ? TEXT.workbench.removal.confirmAll
              : TEXT.workbench.removal.confirm
          }
          cancelLabel={TEXT.workbench.removal.cancel}
          onConfirm={confirmRemoval}
          onCancel={() => {
            setPendingRemoval(null);
          }}
        >
          <p>
            {pendingRemoval?.kind === "all"
              ? TEXT.workbench.removal.bodyAll
              : TEXT.workbench.removal.bodyOne}
          </p>
        </ConfirmDialog>
      </form>
    </div>
  );
}

/** Rótulo de seção: pequeno e rebaixado; a hierarquia visual não precisa seguir a do HTML. */
const SECTION_LABEL = "text-sm font-semibold text-fg-muted";

/** Volta à Oficina e o título da Bancada; também usado enquanto o catálogo carrega. */
export function WorkbenchHeader({ title }: { readonly title: string }): ReactElement {
  return (
    <div className="flex flex-col gap-2">
      <Link
        to={PATHS.home}
        className="inline-flex min-h-11 items-center gap-1 self-start text-sm font-semibold"
      >
        <ChevronLeft aria-hidden="true" className="size-4" />
        {TEXT.workbench.backHome}
      </Link>
      <FocusHeading>{title}</FocusHeading>
    </div>
  );
}

function limitsHint({ inputLimits }: RecipeForm): string {
  const bytes = (value: number | null): string | null =>
    value === null ? null : formatBytes(value);
  return TEXT.workbench.limitsHint(
    inputLimits.maxInputs,
    bytes(inputLimits.maxTotalBytes),
    bytes(inputLimits.maxFileBytes),
  );
}

function removalTitle(removal: PendingRemoval | null): string {
  if (removal === null) {
    return "";
  }
  return removal.kind === "one"
    ? TEXT.workbench.removal.titleOne(removal.name)
    : TEXT.workbench.removal.titleAll(removal.count);
}

function commandFrom(draft: RecipeDraft<RecipeId>, inputs: RunInputs): RunCommand | null {
  if (!draft.ok || !inputs.ok) {
    return null;
  }
  return { ...draft.command, input_upload_ids: [...inputs.uploadIds] };
}

function clientIssues(inputs: RunInputs, draft: RecipeDraft<RecipeId>): FormIssue[] {
  return [
    ...(inputs.ok ? [] : [{ fieldId: FILES_FIELD_ID, message: inputs.message }]),
    ...(draft.ok ? [] : draft.issues),
  ];
}

function failureOf(error: Error): DisplayableFailure {
  return error instanceof ApiError ? error.failure : { kind: "internal" };
}

/**
 * Erros por campo de um VALIDATION_FAILED, com o texto de `fieldErrorText`; `null`
 * para as outras falhas, que vão para o alerta com texto próprio.
 */
function fieldIssuesOf(failure: DisplayableFailure, recipe: RecipeModule): FormIssue[] | null {
  if (failure.kind !== "problem" || failure.problem.code !== "VALIDATION_FAILED") {
    return null;
  }
  const errors = failure.problem.errors ?? [];
  if (errors.length === 0) {
    return null;
  }
  return errors.map(({ field, code, message }) => ({
    fieldId: fieldIdForServerPath(field, recipe),
    message: fieldErrorText(code, message),
  }));
}

const SERVER_FIELD_ERRORS: Readonly<Record<string, string>> = TEXT.workbench.serverFieldErrors;
const VALUE_ERROR_PREFIX = "Value error, ";

/**
 * Texto de um erro por campo. `value_error` traz a regra de negócio escrita pelo próprio GoblinShift
 * (pt-BR, sem eco da entrada além dos nomes de coluna da pessoa), sem o prefixo do Pydantic; os
 * outros códigos são do Pydantic, em inglês, e ganham texto próprio.
 */
function fieldErrorText(code: string, message: string): string {
  if (code === "value_error") {
    return message.startsWith(VALUE_ERROR_PREFIX)
      ? message.slice(VALUE_ERROR_PREFIX.length)
      : message;
  }
  const known = Object.hasOwn(SERVER_FIELD_ERRORS, code) ? SERVER_FIELD_ERRORS[code] : undefined;
  return known ?? TEXT.workbench.serverFieldErrors.fallback;
}

function fieldIdForServerPath(field: string, recipe: RecipeModule): string | null {
  // O servidor aponta o campo a partir do corpo: `body.parameters.filters.0.value`.
  const segments = field.split(".");
  const [root, ...rest] = segments[0] === "body" ? segments.slice(1) : segments;
  if (root === "parameters") {
    return recipe.fieldIdFor(rest);
  }
  return root === "input_upload_ids" ? FILES_FIELD_ID : null;
}

/**
 * Arquivos prontos, na ordem da lista, como o formulário da receita os cita. A referência só
 * muda quando a lista muda: o formulário do PDF Press recalcula o rascunho a cada mudança dela.
 */
function useStableInputs(entries: readonly UploadEntry[]): readonly RecipeInput[] {
  const ready = entries.flatMap((entry) =>
    entry.state.status === "ready"
      ? [{ uploadId: entry.state.uploadId, name: entry.file.name }]
      : [],
  );
  const key = JSON.stringify(ready);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- a chave é o conteúdo da lista.
  return useMemo(() => ready, [key]);
}

function localIdOf(entry: UploadEntry): string {
  return entry.localId;
}

type StatusById = ReadonlyMap<string, UploadEntry["state"]["status"]>;

/** Anúncio de envio concluído ou falho, recalculado só quando algum arquivo muda de estado. */
function useUploadAnnouncement(entries: readonly UploadEntry[]): string | null {
  const [tracked, setTracked] = useState<{ statuses: StatusById; message: string | null }>({
    statuses: new Map(),
    message: null,
  });
  const statuses: StatusById = new Map(entries.map((entry) => [entry.localId, entry.state.status]));
  if (!sameStatuses(tracked.statuses, statuses)) {
    // Ajuste de estado durante a renderização: o padrão do React para estado derivado de dados.
    setTracked({
      statuses,
      message: uploadAnnouncement(tracked.statuses, entries) ?? tracked.message,
    });
  }
  return tracked.message;
}

function sameStatuses(previous: StatusById, next: StatusById): boolean {
  return (
    previous.size === next.size &&
    [...next].every(([localId, status]) => previous.get(localId) === status)
  );
}
