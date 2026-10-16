import { useId, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router";

import { PATHS } from "../app/paths";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { Card } from "../components/ui/Card";
import { TEXT } from "../text";

const T = TEXT.apiGuide;

type StepId = keyof typeof T.steps;

interface Step {
  readonly id: StepId;
  readonly scope: string;
  readonly command: (origin: string) => string;
}

const AUTH = '  -H "Authorization: Bearer $GS_API_KEY"';

/**
 * Do upload ao download, na ordem da API. Cada comando é completo e copiável: a chave sai de
 * `$GS_API_KEY` e os ids, das variáveis que o passo anterior preencheu.
 */
const STEPS: readonly Step[] = [
  {
    id: "intent",
    scope: "files:write",
    command: (origin) =>
      [
        `UPLOAD_ID=$(curl -sS -X POST "${origin}/api/v1/uploads" \\`,
        `${AUTH} \\`,
        '  -H "Content-Type: application/json" \\',
        '  -d "{\\"items\\": [{\\"name\\": \\"clientes.csv\\", \\"size\\": $(wc -c < clientes.csv)}]}" \\',
        "  | jq -r '.items[0].id')",
      ].join("\n"),
  },
  {
    id: "content",
    scope: "files:write",
    command: (origin) =>
      [
        `curl -sS -X PUT "${origin}/api/v1/uploads/$UPLOAD_ID/content" \\`,
        `${AUTH} \\`,
        '  -H "Content-Type: application/octet-stream" \\',
        "  --data-binary @clientes.csv",
      ].join("\n"),
  },
  {
    id: "run",
    scope: "runs:write",
    command: (origin) =>
      [
        "IDEMPOTENCY_KEY=$(uuidgen)",
        `RUN_ID=$(curl -sS -X POST "${origin}/api/v1/runs" \\`,
        `${AUTH} \\`,
        '  -H "Content-Type: application/json" \\',
        '  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \\',
        '  -d "{\\"recipe_id\\": \\"csv-janitor\\", \\"recipe_version\\": 1, \\"input_upload_ids\\": [\\"$UPLOAD_ID\\"], \\"parameters\\": {}}" \\',
        "  | jq -r '.id')",
      ].join("\n"),
  },
  {
    id: "poll",
    scope: "runs:read",
    command: (origin) => [`curl -sS -i "${origin}/api/v1/runs/$RUN_ID" \\`, AUTH].join("\n"),
  },
  {
    id: "results",
    scope: "runs:read",
    command: (origin) =>
      [
        `ARTIFACT_ID=$(curl -sS "${origin}/api/v1/runs/$RUN_ID/results" \\`,
        `${AUTH} \\`,
        "  | jq -r '.artifacts[] | select(.role == \"package\") | .artifact_id')",
      ].join("\n"),
  },
  {
    id: "download",
    scope: "files:read",
    command: (origin) =>
      [`curl -sS -OJ "${origin}/api/v1/artifacts/$ARTIFACT_ID/download" \\`, AUTH].join("\n"),
  },
];

/**
 * Guia da API com chave: página de consulta, sem chamada à API, aberta também sem sessão. Os
 * comandos usam a origem desta página, então servem igual na VPS e no Compose local.
 */
export function ApiGuidePage(): ReactElement {
  const origin = window.location.origin;
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
      <div className="flex flex-col gap-4">
        <FocusHeading>{T.title}</FocusHeading>
        <p className="max-w-prose text-fg-muted">{T.intro}</p>
        <ul className="flex flex-col gap-2">
          {/* /docs e /openapi.json são do servidor da API, não rotas da SPA: navegação normal. */}
          <li>
            <a href="/docs" className="inline-flex min-h-11 items-center font-semibold">
              {T.docsLink}
            </a>
          </li>
          <li>
            <a href="/openapi.json" className="inline-flex min-h-11 items-center font-semibold">
              {T.openApiLink}
            </a>
          </li>
        </ul>
      </div>
      <GuideSection title={T.beforeHeading}>
        <ul className="flex max-w-prose list-disc flex-col gap-2 pl-6">
          {T.before.map((item) => (
            <li key={item}>{withCode(item)}</li>
          ))}
        </ul>
        <p>
          <Link to={PATHS.access} className="inline-flex min-h-11 items-center font-semibold">
            {T.createKeyLink}
          </Link>
        </p>
      </GuideSection>
      <GuideSection title={T.scopesHeading}>
        <ScopesTable />
        <p className="max-w-prose text-sm text-fg-muted">{withCode(T.scopesNote)}</p>
      </GuideSection>
      <GuideSection title={T.stepsHeading}>
        <ol aria-label={T.stepsHeading} className="flex flex-col gap-8">
          {STEPS.map((step, index) => (
            <StepItem key={step.id} step={step} number={index + 1} origin={origin} />
          ))}
        </ol>
      </GuideSection>
      <GuideSection title={T.limitsHeading}>
        <ul className="flex max-w-prose list-disc flex-col gap-2 pl-6">
          {T.limits.map((item) => (
            <li key={item}>{withCode(item)}</li>
          ))}
        </ul>
      </GuideSection>
      <GuideSection title={T.errorsHeading}>
        <p className="max-w-prose">{withCode(T.errors)}</p>
      </GuideSection>
    </div>
  );
}

function GuideSection({
  title,
  children,
}: {
  readonly title: string;
  readonly children: ReactNode;
}): ReactElement {
  const headingId = useId();
  return (
    <Card as="section" aria-labelledby={headingId}>
      <div className="flex flex-col gap-4">
        <h2 id={headingId} className="font-display text-xl font-semibold text-fg">
          {title}
        </h2>
        {children}
      </div>
    </Card>
  );
}

function ScopesTable(): ReactElement {
  return (
    // Tabela larga no celular: rola dentro do próprio contêiner, que o teclado alcança.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- caixa rolável precisa de foco para o teclado
    <div role="region" aria-label={T.scopesCaption} tabIndex={0} className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{T.scopesCaption}</caption>
        <thead className="text-fg-muted">
          <tr>
            <th scope="col" className="py-2 pr-4 font-semibold">
              {T.scopeColumns.scope}
            </th>
            <th scope="col" className="py-2 pr-4 font-semibold">
              {T.scopeColumns.allows}
            </th>
            <th scope="col" className="py-2 font-semibold">
              {T.scopeColumns.routes}
            </th>
          </tr>
        </thead>
        <tbody>
          {T.scopes.map((row) => (
            <tr key={row.scope} className="border-t border-line align-top">
              <th scope="row" className="py-2 pr-4 font-mono font-semibold">
                {row.scope}
              </th>
              <td className="py-2 pr-4">{row.allows}</td>
              <td className="py-2 font-mono text-fg-muted">{row.routes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface StepItemProps {
  readonly step: Step;
  readonly number: number;
  readonly origin: string;
}

function StepItem({ step, number, origin }: StepItemProps): ReactElement {
  const { title, body } = T.steps[step.id];
  return (
    <li className="flex flex-col gap-3">
      <h3 className="text-lg font-semibold text-fg">
        {/* O número fica no título: a lista é estilizada sem marcador, e o título é o que se lê. */}
        {`${String(number)}. ${title}`}
      </h3>
      <p className="max-w-prose">{withCode(body)}</p>
      <p className="text-sm text-fg-muted">{T.scopeOfStep(step.scope)}</p>
      {/* Linhas longas não quebram (o comando tem que continuar copiável): o bloco rola, e o
          tabIndex deixa o teclado rolar também (axe scrollable-region-focusable). */}
      <div
        role="region"
        aria-label={T.commandOf(title)}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- caixa rolável precisa de foco para o teclado
        tabIndex={0}
        className="overflow-x-auto rounded-lg bg-surface-sunken p-4"
      >
        <pre className="font-mono text-sm">
          <code>{step.command(origin)}</code>
        </pre>
      </div>
    </li>
  );
}

/** Texto com trechos entre crases em `<code>`; as crases não aparecem. */
function withCode(text: string): ReactNode[] {
  return text.split("`").map((part, index) =>
    index % 2 === 1 ? (
      <code key={index} className="font-mono text-sm">
        {part}
      </code>
    ) : (
      part
    ),
  );
}
