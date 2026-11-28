import { CircleAlert } from "lucide-react";
import type { ReactElement } from "react";

import type { ApiProblem } from "../api/problem";
import type { HeadingLevel } from "../components/a11y/Heading";
import { asGuidanceCode, GuidanceNotice } from "../components/feedback/FailureNotice";
import { TEXT } from "../text";

interface DownloadFailureNoticeProps {
  readonly code: ApiProblem["code"];
  /** Nível do título dos avisos de login e de termos, conforme a seção onde aparecem. */
  readonly headingLevel: HeadingLevel;
}

/**
 * O download por navegação que falhou e voltou à tela com `?falha=<code>`. Sem sessão, com login
 * antigo demais ou sem o aceite dos termos, a pessoa recebe o caminho para seguir, como no
 * FailureNotice; os demais códigos viram um alerta que diz que foi o download e o motivo.
 */
export function DownloadFailureNotice({
  code,
  headingLevel,
}: DownloadFailureNoticeProps): ReactElement {
  const guidance = asGuidanceCode(code);
  if (guidance !== null) {
    return <GuidanceNotice code={guidance} headingLevel={headingLevel} />;
  }
  return (
    <div
      role="alert"
      className="flex gap-3 rounded-lg border-l-4 border-danger-accent bg-danger-bg p-4 text-danger-fg"
    >
      <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      <p className="font-semibold">
        {TEXT.run.results.downloadFailed(TEXT.failure.problems[code])}
      </p>
    </div>
  );
}
