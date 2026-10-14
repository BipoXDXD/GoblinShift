"""Executor de mentira da imagem de teste do executor-ctl; roda DENTRO do container.

O modo vem de /work/job.json, como o executor real lê a configuração da tentativa:
- `write`: grava out/result.txt e progress.jsonl e sai com 0;
- `sleep`: dorme até ser parado (como PID 1, ignora o SIGTERM e só o SIGKILL o encerra);
- `network`: tenta sair para a rede e grava o que conseguiu em out/network.json.
"""

import json
import socket
import sys
import time
from pathlib import Path

SLEEP_S = 300
OUTBOUND_TARGET = ("1.1.1.1", 53)
NETWORK_TIMEOUT_S = 3


def main() -> None:
    work = Path(sys.argv[1])
    mode = json.loads((work / "job.json").read_text())["mode"]
    if mode == "write":
        (work / "out" / "result.txt").write_text("ok\n")
        (work / "progress.jsonl").write_text('{"phase": "done"}\n')
    elif mode == "sleep":
        time.sleep(SLEEP_S)
    elif mode == "network":
        (work / "out" / "network.json").write_text(json.dumps(probe_network()))
    else:
        sys.exit(f"modo desconhecido: {mode}")


def probe_network() -> dict[str, object]:
    interfaces = sorted(name for _, name in socket.if_nameindex())
    try:
        socket.create_connection(OUTBOUND_TARGET, timeout=NETWORK_TIMEOUT_S).close()
        outbound_error = None
    except OSError as error:
        outbound_error = type(error).__name__
    try:
        socket.getaddrinfo("gvisor.dev", 443)
        dns_error = None
    except OSError as error:
        dns_error = type(error).__name__
    return {"interfaces": interfaces, "outbound_error": outbound_error, "dns_error": dns_error}


if __name__ == "__main__":
    main()
