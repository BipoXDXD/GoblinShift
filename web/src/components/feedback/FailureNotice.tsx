import { FileText } from "lucide-react";
import { useId, type ReactElement } from "react";
import { Link, useLocation } from "react-router";

import { safeReturnTo } from "../../account/signIn";
import { SignInPrompt } from "../../account/SignInPrompt";
import { ApiError } from "../../api/response";
import { acceptTermsPath } from "../../app/paths";
import { TEXT } from "../../text";
import { ProblemAlert, type ProblemMessages } from "./ProblemAlert";

interface FailureNoticeProps {
  readonly error: Error;
  /** Nível do título dos avisos de login e de termos, conforme a seção onde aparecem. */
  readonly headingLevel?: 2 | 3;
  readonly messages?: ProblemMessages;
}

/**
 * Falha de uma leitura ou de uma ação. Sem sessão, com login antigo demais ou sem o aceite dos
 * termos, a pessoa recebe o caminho para seguir (entrar, entrar de novo, aceitar), não um erro; o
 * resto é o ProblemAlert. Erro que não veio da API é defeito da interface (`internal`).
 */
export function FailureNotice({
  error,
  headingLevel = 2,
  messages,
}: FailureNoticeProps): ReactElement {
  if (!(error instanceof ApiError)) {
    return <ProblemAlert failure={{ kind: "internal" }} />;
  }
  const code = guidanceCode(error);
  if (code === "AUTHENTICATION_REQUIRED" || code === "REAUTH_REQUIRED") {
    return <SignInPrompt reauth={code === "REAUTH_REQUIRED"} headingLevel={headingLevel} />;
  }
  if (code === "TERMS_NOT_ACCEPTED") {
    return <TermsRequiredNotice headingLevel={headingLevel} />;
  }
  return <ProblemAlert failure={error.failure} {...(messages === undefined ? {} : { messages })} />;
}

/** Códigos em que a falha vira um caminho (entrar, entrar de novo, aceitar), não um alerta. */
const GUIDANCE_CODES = [
  "AUTHENTICATION_REQUIRED",
  "REAUTH_REQUIRED",
  "TERMS_NOT_ACCEPTED",
] as const;
type GuidanceCode = (typeof GUIDANCE_CODES)[number];

/**
 * Texto a anunciar quando o FailureNotice mostra um caminho (entrar, entrar de novo, aceitar), ou
 * `null` quando mostra o ProblemAlert. O caminho é informativo, sem `role="alert"`: numa tela que o
 * troca sozinha, como o polling da Execução, quem a usa anuncia este texto na própria região viva.
 */
export function guidanceAnnouncement(error: Error): string | null {
  const code = guidanceCode(error);
  return code === null ? null : TEXT.failure.problems[code];
}

function guidanceCode(error: Error): GuidanceCode | null {
  if (!(error instanceof ApiError) || error.failure.kind !== "problem") {
    return null;
  }
  const { code } = error.failure.problem;
  return GUIDANCE_CODES.find((guidance) => guidance === code) ?? null;
}

/** A conta precisa aceitar a versão nova dos termos; o link volta para esta tela depois. */
function TermsRequiredNotice({ headingLevel }: { readonly headingLevel: 2 | 3 }): ReactElement {
  const titleId = useId();
  const { pathname, search } = useLocation();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section
      aria-labelledby={titleId}
      className="flex gap-3 rounded-lg border-l-4 border-info-fg bg-info-bg p-4 text-info-fg"
    >
      <FileText aria-hidden="true" className="mt-1 size-4 flex-none" />
      <div className="flex max-w-prose flex-col gap-1">
        <Heading id={titleId} className="font-semibold">
          {TEXT.terms.requiredTitle}
        </Heading>
        <p>{TEXT.terms.requiredBody}</p>
        <p>
          <Link
            to={acceptTermsPath(safeReturnTo(pathname, search))}
            className="inline-flex min-h-11 items-center font-semibold"
          >
            {TEXT.terms.goToAccept}
          </Link>
        </p>
      </div>
    </section>
  );
}
