"""Claim só com a área de trabalho livre (3.4 e 3.6; complemento do #224).

O `executor-ctl` recusa o `start` (`work_area_dirty`) enquanto a área guarda outra tentativa: a
área inteira é a cota de uma tentativa (ADR 005). Uma tentativa órfã (worker morto por SIGKILL ou
deploy, ou falha de banco fora da supervisão) deixa a área ocupada até a manutenção parar o
executor e removê-la. Reivindicar nesse intervalo gasta o claim da Run seguinte por um defeito que
não é dela.

A pausa de `claim_pause.py` cobre o processo que viu a falha, mas vive na memória e se perde no
reinício. O portão confere o próprio estado: antes de cada claim, o worker lista as tentativas com
área (`Runner.list_work`, a mesma área que ele prepara e o `executor-ctl` confere). Com alguma, ou
com a área ilegível, a volta não reivindica.

A espera tem teto (`HOLD_LIMIT`), contado no relógio monotônico desde que o worker viu a área
presa pela primeira vez e zerado quando ela esvazia. Passado o teto, o portão libera o claim: o
`start` é recusado como antes, e o alerta `executor_start_failed`, que já existe, avisa alguém. Sem
isso, uma área presa de vez pararia a fila sem sinal nenhum (os logs não são exportados).

`list_work` só reconhece nomes de tentativa (UUID canônico). Outra entrada na área ainda passa
pelo portão e é recusada no `start`, como antes.
"""

import enum
import logging
import time
from collections.abc import Callable
from datetime import timedelta

from goblinshift.runs.worker.runner import Runner

logger = logging.getLogger(__name__)

# Quantas tentativas o aviso cita; o total vai sempre no campo `attempts`.
LOGGED_ATTEMPTS = 5
# Duas vezes a janela de limpeza de uma órfã (`orphan_cleanup_window`: lease, ciclo da manutenção
# e carência da parada, 100 s com os prazos de produção). Uma área presa além disso não está mais
# esperando a limpeza normal; liberar o claim devolve o problema ao alerta do `start` recusado.
HOLD_LIMIT = timedelta(seconds=200)


class _AreaState(enum.Enum):
    CLEAR = "clear"
    OCCUPIED = "occupied"
    UNREADABLE = "unreadable"
    PAST_LIMIT = "past_limit"


class WorkAreaGate:
    """Libera o claim com a área sem tentativa, ou quando a espera passa do teto.

    Registra a mudança de estado, não cada consulta: área ocupada, ilegível (o traceback só na
    primeira vez), teto atingido e a retomada quando a área esvazia.
    """

    def __init__(self, runner: Runner, clock: Callable[[], float] = time.monotonic) -> None:
        self._runner = runner
        self._clock = clock
        self._limit_s = HOLD_LIMIT.total_seconds()
        self._state = _AreaState.CLEAR
        self._held_since: float | None = None

    def allows_claim(self) -> bool:
        try:
            leftover = self._runner.list_work()
        except OSError:
            self._hold(_AreaState.UNREADABLE, fields=None)
            return self._state is _AreaState.PAST_LIMIT
        if not leftover:
            self._release()
            return True
        fields = {
            "attempts": len(leftover),
            "attempt_ids": [str(attempt_id) for attempt_id in leftover[:LOGGED_ATTEMPTS]],
        }
        self._hold(_AreaState.OCCUPIED, fields=fields)
        return self._state is _AreaState.PAST_LIMIT

    def _hold(self, state: _AreaState, fields: dict[str, object] | None) -> None:
        """Marca a área presa; passado o teto, o estado vira `PAST_LIMIT` até ela esvaziar."""
        now = self._clock()
        if self._held_since is None:
            self._held_since = now
        held_s = now - self._held_since
        if held_s >= self._limit_s:
            if self._state is not _AreaState.PAST_LIMIT:
                logger.warning(
                    "área de trabalho presa além do teto; claims liberados",
                    extra={"fields": {"held_s": held_s, "limit_s": self._limit_s}},
                )
            self._state = _AreaState.PAST_LIMIT
            return
        if state is not self._state:
            self._log_hold(state, fields)
        self._state = state

    def _log_hold(self, state: _AreaState, fields: dict[str, object] | None) -> None:
        if state is _AreaState.UNREADABLE:
            # Dentro do `except` de `allows_claim`: o traceback do `OSError` vai junto.
            logger.warning("área de trabalho ilegível; claims esperando", exc_info=True)
        else:
            logger.warning("claims esperando a área de trabalho", extra={"fields": fields})

    def _release(self) -> None:
        if self._state is not _AreaState.CLEAR:
            logger.info("área de trabalho livre; claims retomados")
        self._state = _AreaState.CLEAR
        self._held_since = None
