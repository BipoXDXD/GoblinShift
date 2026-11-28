import { Wifi, WifiOff } from "lucide-react";
import { useEffect, useState, type ReactElement } from "react";

import { TEXT } from "../text";
import { useOnlineStatus } from "./useOnlineStatus";

const T = TEXT.connection;

/** Quanto tempo o "Conexão de volta." fica na tela antes de a faixa sumir. */
export const RECONNECTED_MS = 5_000;

// As mesmas medidas da faixa do aviso global (SiteNoticeBanner): fio inferior, largura e ícone.
const BAND = "border-b-2";
const ROW = "mx-auto flex w-full max-w-5xl items-start gap-3 px-4 py-4 md:px-8";
const ICON = "mt-1 size-4 flex-none";
// Âmbar (aviso) sem conexão; informação quando ela volta. O ícone e o texto dizem o estado, não só
// a cor (WCAG 1.4.1).
const OFFLINE_BAND = "border-warning-accent bg-warning-bg text-warning-fg";
const BACK_BAND = "border-info-fg bg-info-bg text-info-fg";

/**
 * Faixa de "sem conexão" entre a barra e o conteúdo. Sem internet o TanStack Query pausa queries e
 * mutations, e a tela ficaria em "Carregando" sem motivo: a faixa diz o que acontece e, quando a
 * conexão volta, diz isso por `RECONNECTED_MS` e some. A região viva fica sempre montada, como a
 * do LiveRegion, para o leitor de tela anunciar a mudança; a página que abre online não mostra nada.
 */
export function ConnectionNotice(): ReactElement {
  const online = useOnlineStatus();
  const reconnected = useReconnected(online);
  return (
    <div role="status" aria-live="polite" aria-atomic="true">
      {online ? reconnected && <ReconnectedBand /> : <OfflineBand />}
    </div>
  );
}

function OfflineBand(): ReactElement {
  return (
    <div className={`${BAND} ${OFFLINE_BAND}`}>
      <div className={ROW}>
        <WifiOff aria-hidden="true" className={ICON} />
        <div>
          <p className="font-semibold">{T.offline}</p>
          <p className="max-w-prose">{T.offlineDetail}</p>
        </div>
      </div>
    </div>
  );
}

function ReconnectedBand(): ReactElement {
  return (
    <div className={`${BAND} ${BACK_BAND}`}>
      <div className={ROW}>
        <Wifi aria-hidden="true" className={ICON} />
        <p className="font-semibold">{T.back}</p>
      </div>
    </div>
  );
}

/** Verdadeiro nos `RECONNECTED_MS` depois de a conexão voltar; cair de novo encerra o aviso. */
function useReconnected(online: boolean): boolean {
  const [lastOnline, setLastOnline] = useState(online);
  const [reconnected, setReconnected] = useState(false);
  // Ajuste de estado no render, quando a conexão muda: sem efeito extra nem pintura intermediária.
  if (online !== lastOnline) {
    setLastOnline(online);
    setReconnected(online);
  }
  useEffect(() => {
    if (!reconnected) {
      return undefined;
    }
    const timer = setTimeout(() => {
      setReconnected(false);
    }, RECONNECTED_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [reconnected]);
  return reconnected;
}
