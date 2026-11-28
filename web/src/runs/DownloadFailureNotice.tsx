import type { ReactElement } from "react";

import type { ApiProblem } from "../api/problem";
import type { HeadingLevel } from "../components/a11y/Heading";
import { DangerAlert } from "../components/feedback/DangerAlert";
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
    <DangerAlert>
      <p className="font-semibold">
        {TEXT.run.results.downloadFailed(TEXT.failure.problems[code])}
      </p>
    </DangerAlert>
  );
}
