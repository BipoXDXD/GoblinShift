import { useMutation } from "@tanstack/react-query";
import { useId, type ReactElement } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TEXT } from "../text";
import { EXPORT_FILE_NAME, exportMe } from "./api";

const T = TEXT.account.export;

/**
 * Entrega o arquivo ao navegador por um link temporário. A URL é liberada na volta seguinte do
 * event loop: revogada no mesmo tique, há navegador que cancela o download.
 */
function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

/**
 * Seus dados (GET /me/export): baixa em JSON o que a conta guarda. Pede login recente, como a
 * exclusão: o REAUTH_REQUIRED vira o convite para entrar de novo e voltar ao Acesso.
 */
export function ExportSection(): ReactElement {
  const headingId = useId();
  const client = useApiClient();
  const download = useMutation({
    mutationFn: () => exportMe(client),
    onSuccess: (blob) => {
      saveFile(blob, EXPORT_FILE_NAME);
    },
  });
  return (
    <Card as="section" aria-labelledby={headingId}>
      <div className="flex flex-col gap-4">
        <h2 id={headingId} className="text-lg font-semibold text-fg">
          {T.heading}
        </h2>
        <p className="max-w-prose text-fg-muted">{T.body}</p>
        {download.error !== null && <FailureNotice error={download.error} headingLevel={3} />}
        <div>
          <Button
            type="button"
            variant="secondary"
            aria-disabled={download.isPending}
            onClick={() => {
              if (!download.isPending) {
                download.mutate();
              }
            }}
          >
            {download.isPending ? T.downloading : T.download}
          </Button>
        </div>
      </div>
    </Card>
  );
}
