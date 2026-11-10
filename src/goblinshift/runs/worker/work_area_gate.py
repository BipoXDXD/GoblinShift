"""Claim só com a área de trabalho livre (3.4 e 3.6; complemento do #224).

O `executor-ctl` recusa o `start` (`work_area_dirty`) enquanto a área guarda outra tentativa: a
área inteira é a cota de uma tentativa (ADR 005). Uma tentativa órfã (worker morto por SIGKILL ou
deploy, ou falha de banco fora da supervisão) deixa a área ocupada até a manutenção parar o
executor e removê-la. Reivindicar nesse intervalo gasta o claim da Run seguinte por um defeito que
não é dela.

A pausa de `claim_pause.py` cobre o processo que viu a falha, mas vive na memória e se perde no
reinício. O portão confere o próprio estado: antes de cada claim, o worker lista as tentativas com
área (`Runner.list_work`, a mesma área que ele prepara e o `executor-ctl` confere). Com alguma, a
volta não reivindica. Área ilegível sobe o erro, e a volta também não reivindica.

`list_work` só reconhece nomes de tentativa (UUID canônico). Outra entrada na área ainda passa
pelo portão e é recusada no `start`, como antes.
"""

import logging

from goblinshift.runs.worker.runner import Runner

logger = logging.getLogger(__name__)

# Quantas tentativas o aviso cita; o total vai sempre no campo `attempts`.
LOGGED_ATTEMPTS = 5


class WorkAreaGate:
    """Libera o claim só com a área de trabalho sem tentativa.

    Registra a mudança de estado, não cada consulta: um aviso quando a área fica ocupada e uma
    linha de retomada quando ela esvazia.
    """

    def __init__(self, runner: Runner) -> None:
        self._runner = runner
        self._occupied = False

    def allows_claim(self) -> bool:
        leftover = self._runner.list_work()
        if leftover and not self._occupied:
            fields = {
                "attempts": len(leftover),
                "attempt_ids": [str(attempt_id) for attempt_id in leftover[:LOGGED_ATTEMPTS]],
            }
            logger.warning("claims esperando a área de trabalho", extra={"fields": fields})
        elif not leftover and self._occupied:
            logger.info("área de trabalho livre; claims retomados")
        self._occupied = bool(leftover)
        return not leftover
