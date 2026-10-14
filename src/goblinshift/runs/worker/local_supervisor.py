"""Supervisor do executor local: `python -m goblinshift.runs.worker.local_supervisor`.

Argumentos: `<meta_dir> <timeout_s> <grace_s> <comando do executor…>`. Lançado pelo
`LocalRunner` como líder de um grupo de processos novo; ver o docstring de `runner` para o
porquê do desdobramento.

1. O lançador grava o ID do grupo em `meta_dir/pgid` e se desdobra; o pai sai na hora.
2. O neto (supervisor) roda o executor no mesmo grupo, espera até `timeout_s`, e no estouro
   manda SIGTERM, espera `grace_s` e manda SIGKILL.
3. Grava `meta_dir/exit.json` (`{"exit_code": n, "timed_out": bool}`) de forma atômica.
"""

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from goblinshift.runs.worker.runner import EXIT_FILE, PGID_FILE

_MIN_ARGS = 4
EXIT_USAGE = 2


def main(argv: Sequence[str]) -> int:
    if len(argv) < _MIN_ARGS:
        print("uso: local_supervisor <meta_dir> <timeout_s> <grace_s> <comando…>", file=sys.stderr)
        return EXIT_USAGE
    meta = Path(argv[0])
    timeout_s, grace_s = float(argv[1]), float(argv[2])
    command = list(argv[3:])
    _write_atomic(meta / PGID_FILE, str(os.getpgrp()))
    if os.fork() > 0:
        return 0
    # Neto: órfão daqui em diante, colhido pelo init quando terminar.
    exit_code, timed_out = _supervise(command, timeout_s, grace_s)
    _write_atomic(meta / EXIT_FILE, json.dumps({"exit_code": exit_code, "timed_out": timed_out}))
    os._exit(0)


def _supervise(command: list[str], timeout_s: float, grace_s: float) -> tuple[int, bool]:
    executor = subprocess.Popen(command, stdin=subprocess.DEVNULL)  # noqa: S603 - do Runner
    try:
        return executor.wait(timeout=timeout_s), False
    except subprocess.TimeoutExpired:
        executor.terminate()
        try:
            return executor.wait(timeout=grace_s), True
        except subprocess.TimeoutExpired:
            executor.kill()
            return executor.wait(), True


def _write_atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
