"""Cliente mínimo do protocolo, para o teste de aceitação e os testes do executor-ctl.

O worker tem o cliente dele em `goblinshift.runs`; este existe para quem roda no host.
"""

import http.client
import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 60.0


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        super().__init__("localhost", timeout=timeout_s)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(str(self._socket_path))
        self.sock = sock


@dataclass(frozen=True)
class Response:
    status: int
    # Vazio no 204 do remove.
    body: dict[str, Any]


def call(
    socket_path: Path,
    method: str,
    path: str,
    body: object = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Response:
    payload = None if body is None else json.dumps(body).encode()
    headers = {} if payload is None else {"Content-Type": "application/json"}
    connection = UnixHTTPConnection(socket_path, timeout_s)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
    finally:
        connection.close()
    parsed = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        raise ValueError(f"resposta do executor-ctl não é objeto JSON (HTTP {response.status})")
    return Response(response.status, parsed)
