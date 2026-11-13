"""Executor de teste: provoca lentidão e falhas e, quando cabe, delega ao executor real.

    python tests/runs/fake_executor.py <modo> [argumento] <work_dir>

- `sleep <s>`: avisa na saída (`SLEEPING`), dorme e então roda o executor real (janela para
  cancelar, pausar ou matar).
- `fail-once <marcador>`: na primeira vez cria o marcador e sai com 4 (infraestrutura); depois
  roda o executor real.
- `exit <código>`: sai com o código, sem tocar no diretório.
- `fail-deterministic <código>`: escreve `failure.json` com o código e sai com 3.
- `fail-raw <conteúdo>`: escreve `failure.json` com o conteúdo literal e sai com 3.
- `ignore-term <s>`: ignora SIGTERM, avisa na saída (`IGNORING_TERM`) e dorme (obriga o SIGKILL
  depois da carência).
- `tamper-package`: roda o executor real e acrescenta ao ZIP de out/ uma entrada que o
  empacotador nunca gravaria (executor comprometido).
- `oversize-report`: roda o executor real e troca o `report.json` de out/ por um arquivo um byte
  acima do teto do relatório (executor comprometido ou defeituoso).
"""

import json
import signal
import sys
import time
import zipfile
from pathlib import Path

from goblinshift.processing.executor.execution import main as real_executor
from goblinshift.processing.executor.report_file import REPORT_MAX_BYTES

# Linha que o modo `ignore-term` escreve depois de instalar o SIG_IGN: daí em diante o SIGTERM
# não o derruba.
IGNORING_TERM = "SIGTERM ignorado"
# Linha que o modo `sleep` escreve antes de dormir: daí em diante o executor está de pé, e um
# sinal não disputa mais com a partida do supervisor e do interpretador.
SLEEPING = "dormindo"


def run(argv: list[str]) -> int:
    mode, *rest = argv
    work_dir = rest[-1]
    match mode:
        case "sleep":
            print(SLEEPING, flush=True)
            time.sleep(float(rest[0]))
            return real_executor([work_dir])
        case "fail-once":
            marker = Path(rest[0])
            if not marker.exists():
                marker.touch()
                return 4
            return real_executor([work_dir])
        case "exit":
            return int(rest[0])
        case "fail-deterministic":
            failure = {"code": rest[0], "detail": "falha provocada pelo teste"}
            (Path(work_dir) / "failure.json").write_text(json.dumps(failure), encoding="utf-8")
            return 3
        case "fail-raw":
            (Path(work_dir) / "failure.json").write_text(rest[0], encoding="utf-8")
            return 3
        case "tamper-package":
            code = real_executor([work_dir])
            [package] = Path(work_dir, "out").glob("*.zip")
            with zipfile.ZipFile(package, "a") as archive:
                archive.writestr("../../etc/cron.d/x", b"* * * * * root id\n")
            return code
        case "oversize-report":
            code = real_executor([work_dir])
            Path(work_dir, "out", "report.json").write_bytes(b" " * (REPORT_MAX_BYTES + 1))
            return code
        case "ignore-term":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            print(IGNORING_TERM, flush=True)
            time.sleep(float(rest[0]))
            return 0
        case _:
            raise SystemExit(f"modo desconhecido: {mode}")


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
