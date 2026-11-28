import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactElement, type SubmitEvent } from "react";
import { useSearchParams } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { ErrorSummary, type FormIssue } from "../components/a11y/ErrorSummary";
import { Field } from "../components/a11y/Field";
import { FieldGroup } from "../components/a11y/FieldGroup";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import { API_KEYS_QUERY_KEY, createApiKey, SCOPES, type CreatedApiKey, type Scope } from "./api";
import { KEY_DAYS, KEY_LIMITS, parseNewKey, type NewKeyErrors } from "./keys";
import {
  isReauthRequired,
  newKeyChoicesToSearch,
  withoutNewKeyChoices,
  type NewKeyChoices,
} from "./newKeyParams";

const T = TEXT.account.keys;
const NO_ERRORS: NewKeyErrors = { scopes: null, days: null };

interface NewKeyFormProps {
  /** Recebe a chave criada; o segredo fica só no estado de quem mostra, nunca no cache. */
  readonly onCreated: (key: CreatedApiKey) => void;
  readonly onCancel: () => void;
  /** Escolhas que voltaram do login de novo; sem elas, nenhum escopo e a validade inicial. */
  readonly initial?: NewKeyChoices | undefined;
}

/**
 * Escopos e validade da chave nova; o servidor exige login recente (REAUTH_REQUIRED). Nesse caso as
 * escolhas (nunca o segredo) vão para a URL da tela, e o convite para entrar as leva no `return_to`.
 */
export function NewKeyForm({ onCreated, onCancel, initial }: NewKeyFormProps): ReactElement {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const ids = { scopes: useId(), days: useId(), kept: useId() };
  const [, setSearchParams] = useSearchParams();
  const [scopes, setScopes] = useState<ReadonlySet<Scope>>(() => new Set(initial?.scopes ?? []));
  const [days, setDays] = useState(initial?.days ?? String(KEY_DAYS.initial));
  const [errors, setErrors] = useState<NewKeyErrors>(NO_ERRORS);
  const [attempt, setAttempt] = useState(0);
  // "Nova chave" some quando o formulário abre: sem isto, o foco cairia no <body>.
  useEffect(() => {
    heading.current?.focus();
  }, []);
  const create = useMutation({
    mutationFn: (command: Parameters<typeof createApiKey>[1]) => createApiKey(client, command),
    // O segredo não fica na MutationCache depois que esta tela sai (gcTime 0) nem depois de
    // entregue a quem chamou (reset).
    gcTime: 0,
    onSuccess: async (key) => {
      create.reset();
      // As escolhas de um REAUTH anterior saem da URL: a chave já existe.
      setSearchParams(withoutNewKeyChoices, { replace: true });
      onCreated(key);
      await queryClient.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY });
    },
    // O que foi enviado, não o estado atual: é o que o servidor recusou. O roteador aplica a URL
    // numa transição, então o link de entrar pode nascer com a URL velha e se corrigir logo depois.
    onError: (error, command) => {
      if (isReauthRequired(error)) {
        setSearchParams(
          (current) =>
            newKeyChoicesToSearch({ scopes: command.scopes, days: String(command.days) }, current),
          { replace: true },
        );
      }
    },
  });

  const submit = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (create.isPending) {
      return;
    }
    const parsed = parseNewKey({ scopes, days }, SCOPES);
    setAttempt((value) => value + 1);
    if (!parsed.ok) {
      setErrors(parsed.errors);
      return;
    }
    setErrors(NO_ERRORS);
    create.mutate(parsed.command);
  };

  const toggle = (scope: Scope, checked: boolean): void => {
    setScopes((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(scope);
      } else {
        next.delete(scope);
      }
      return next;
    });
  };

  const issues: FormIssue[] = [
    ...(errors.scopes === null
      ? []
      : [{ fieldId: firstScopeId(ids.scopes), label: T.scopes, message: errors.scopes }]),
    ...(errors.days === null ? [] : [{ fieldId: ids.days, label: T.days, message: errors.days }]),
  ];
  return (
    <form
      aria-labelledby={headingId}
      aria-describedby={initial === undefined ? undefined : ids.kept}
      noValidate
      onSubmit={submit}
      className="flex flex-col gap-6 rounded-lg bg-surface-sunken p-6"
    >
      <h3 id={headingId} ref={heading} tabIndex={-1} className="font-semibold text-fg">
        {T.formHeading}
      </h3>
      {initial !== undefined && (
        <p id={ids.kept} className="text-sm text-fg-muted">
          {T.choicesKept}
        </p>
      )}
      <ErrorSummary issues={issues} attempt={attempt} headingLevel={4} />
      <FieldGroup id={ids.scopes} legend={T.scopes} error={errors.scopes}>
        <div className="flex flex-col gap-1">
          {SCOPES.map((scope, index) => (
            <Field
              key={scope}
              id={index === 0 ? firstScopeId(ids.scopes) : `${ids.scopes}-${scope}`}
              label={`${T.scopeLabels[scope]} (${scope})`}
              layout="inline"
            >
              {(control) => (
                <input
                  {...control}
                  type="checkbox"
                  checked={scopes.has(scope)}
                  onChange={(event) => {
                    toggle(scope, event.currentTarget.checked);
                  }}
                />
              )}
            </Field>
          ))}
        </div>
      </FieldGroup>
      <Field
        id={ids.days}
        label={T.days}
        description={T.daysHint(KEY_DAYS.min, KEY_DAYS.max)}
        error={errors.days}
      >
        {(control) => (
          <input
            {...control}
            type="text"
            inputMode="numeric"
            autoComplete="off"
            maxLength={3}
            className="max-w-32"
            value={days}
            onChange={(event) => {
              setDays(event.currentTarget.value);
            }}
          />
        )}
      </Field>
      {create.error !== null && (
        <FailureNotice
          error={create.error}
          headingLevel={4}
          messages={{
            STATE_CONFLICT: T.tooMany(
              KEY_LIMITS.active,
              KEY_LIMITS.stored,
              KEY_LIMITS.retentionDays,
            ),
          }}
        />
      )}
      <div className="flex flex-col gap-3 sm:flex-row">
        <Button type="submit" variant="primary" aria-disabled={create.isPending}>
          {create.isPending ? T.creating : T.create}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          {T.cancel}
        </Button>
      </div>
    </form>
  );
}

function firstScopeId(groupId: string): string {
  return `${groupId}-primeiro`;
}
