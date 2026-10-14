"""Executor de teste: provoca lentidão e falhas e, quando cabe, delega ao executor real.

    python tests/runs/fake_executor.py <modo> [argumento] <work_dir>

- `sleep <s>`: dorme e então roda o executor real (janela para cancelar, pausar ou matar).
- `fail-once <marcador>`: na primeira vez cria o marcador e sai com 4 (infraestrutura); depois
  roda o executor real.
- `exit <código>`: sai com o código, sem tocar no diretório.
- `fail-deterministic <código>`: escreve `failure.json` com o código e sai com 3.
- `ignore-term <s>`: ignora SIGTERM e dorme (obriga o SIGKILL depois da carência).
"""

import json
import signal
import sys
import time
from pathlib import Path

from goblinshift.processing.executor.execution import main as real_executor


def run(argv: list[str]) -> int:
    mode, *rest = argv
    work_dir = rest[-1]
    match mode:
        case "sleep":
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
        case "ignore-term":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(float(rest[0]))
            return 0
        case _:
            raise SystemExit(f"modo desconhecido: {mode}")


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
