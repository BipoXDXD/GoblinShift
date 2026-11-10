"""Pausa dos claims depois de falha de banco que pode deixar tentativa órfã (3.4).

A supervisão absorve a falha transitória do banco na renovação do lease, mas os passos fora dela
(`record_start`, a renovação no empacotamento e o desfecho) deixam a exceção subir até a volta do
worker. A Attempt fica viva, com executor e área de trabalho, até a manutenção encerrá-la pelo
lease vencido. Nesse intervalo o `executor-ctl` recusa outro `start` (`work_area_dirty`), e a Run
seguinte gastaria claims por um defeito que não é dela, podendo chegar a `INFRA_POISON`.

O worker então não reivindica por uma janela que cobre o pior caso dessa limpeza: o lease da órfã
vence no máximo um lease depois da falha, a manutenção a encontra até um intervalo de ciclo depois
e para o executor com a carência da parada. A janela é duração, medida no relógio monotônico.
"""

import time
from collections.abc import Callable
from datetime import timedelta


def orphan_cleanup_window(
    lease: timedelta, maintenance_interval: timedelta, stop_grace: timedelta
) -> timedelta:
    """Tempo até a manutenção encerrar uma tentativa órfã: 60 + 30 + 10 = 100 s em produção."""
    return lease + maintenance_interval + stop_grace


class ClaimPause:
    """Janela sem claims, aberta a cada falha; uma falha nova reinicia a janela."""

    def __init__(self, window: timedelta, clock: Callable[[], float] = time.monotonic) -> None:
        if window < timedelta(0):
            raise ValueError(f"janela da pausa negativa: {window}")
        self._window_s = window.total_seconds()
        self._clock = clock
        self._until = float("-inf")

    @property
    def window(self) -> timedelta:
        return timedelta(seconds=self._window_s)

    @property
    def active(self) -> bool:
        return self._clock() < self._until

    def begin(self) -> None:
        self._until = self._clock() + self._window_s
