"""`executor-ctl` falso, fiel ao contrato da fatia 4, num socket Unix de teste.

Fala HTTP/1.1 com corpo JSON e as quatro operações (`start`, `status`, `stop`, `remove`), com os
mesmos códigos de erro do serviço real. Em vez de criar containers:

- com `command`, o `start` roda `command + [<área>/<id>]` num subprocesso de sessão própria (o
  "container"), e `stop` sinaliza o grupo com SIGTERM, carência e SIGKILL;
- sem `command`, o "container" é só um estado controlado pelo teste (`containers`).

Os controles simulam falhas do serviço: `docker_down` (503), `response_delay_s` (resposta mais
lenta que o timeout do cliente), `unstoppable` (o `stop` não consegue parar) e `close()` (socket
fora do ar). `calls` registra a ordem das operações recebidas.
"""

import contextlib
import json
import os
import re
import shutil
import signal
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
_ROUTE = re.compile(r"^/v1/attempts/(?P<id>[^/]+)(?P<action>/start|/stop)?$")
_KILL_CONFIRM_S = 5.0
_POLL_S = 0.02
_SIGNAL_EXIT_BASE = 128
_RESERVED_ENTRIES = frozenset({"lost+found"})


@dataclass
class FakeContainer:
    state: str = "running"
    exit_code: int | None = None
    oom_killed: bool = False
    process: subprocess.Popen[bytes] | None = None

    def refresh(self) -> None:
        if self.process is None or self.state != "running":
            return
        code = self.process.poll()
        if code is not None:
            self.state = "exited"
            self.exit_code = code if code >= 0 else _SIGNAL_EXIT_BASE - code

    def as_status(self) -> dict[str, object]:
        self.refresh()
        return {
            "state": self.state,
            "exit_code": self.exit_code,
            "oom_killed": self.oom_killed,
            "started_at": "2026-10-14T12:00:00Z",
            "finished_at": "2026-10-14T12:01:00Z" if self.state == "exited" else None,
        }


@dataclass
class FakeExecutorCtl:
    work_area: Path
    allowed_images: Sequence[str] = (IMAGE,)
    command: Sequence[str] | None = None
    containers: dict[str, FakeContainer] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    docker_down: bool = False
    response_delay_s: float = 0.0
    unstoppable: bool = False
    # O executor que terminar sai como morto por falta de memória (`oom_killed`).
    oom_on_exit: bool = False
    # Chamado com o `attempt_id` no início de cada `stop`, antes de parar.
    on_stop: Callable[[str], None] | None = None
    last_start_body: object = None

    def __post_init__(self) -> None:
        # Caminho curto: o socket Unix tem limite de ~104 bytes no macOS.
        self._socket_dir = Path(tempfile.mkdtemp(prefix="gsctl-"))
        self.socket_path = self._socket_dir / "ctl.sock"
        self._lock = threading.Lock()
        self._closed = False
        self._server = _Server(str(self.socket_path), _handler_for(self))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Derruba o serviço: o socket some, como com o systemd parado. Repetir não faz nada."""
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        self._server.server_close()
        self.socket_path.unlink(missing_ok=True)
        for container in self.containers.values():
            if container.process is not None and container.process.poll() is None:
                _signal_group(container.process, signal.SIGKILL)
                container.process.wait()
        shutil.rmtree(self._socket_dir, ignore_errors=True)

    def operations(self, attempt_id: uuid.UUID) -> list[str]:
        return [operation for operation, target in self.calls if target == str(attempt_id)]

    # Operações -------------------------------------------------------------------------

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, dict[str, Any] | None]:
        if self.response_delay_s:
            time.sleep(self.response_delay_s)
        match = _ROUTE.match(path)
        if match is None:
            return 404, _error("not_found")
        raw_id, action = match["id"], match["action"]
        try:
            attempt_id = str(uuid.UUID(raw_id))
        except ValueError:
            return 400, _error("invalid_attempt_id")
        if attempt_id != raw_id:
            return 400, _error("invalid_attempt_id")
        with self._lock:
            match method, action:
                case "POST", "/start":
                    self.calls.append(("start", attempt_id))
                    return self._start(attempt_id, body)
                case "GET", None:
                    self.calls.append(("status", attempt_id))
                    return 200, self._status(attempt_id)
                case "POST", "/stop":
                    self.calls.append(("stop", attempt_id))
                    return self._stop(attempt_id, body)
                case "DELETE", None:
                    self.calls.append(("remove", attempt_id))
                    return self._remove(attempt_id)
                case _:
                    return 405, _error("method_not_allowed")

    def _start(self, attempt_id: str, body: bytes) -> tuple[int, dict[str, Any] | None]:
        document = _json_object(body)
        self.last_start_body = document
        image = None if document is None else document.get("image")
        if document is None or set(document) != {"image"} or not isinstance(image, str):
            return 400, _error("invalid_body")
        if image not in self.allowed_images:
            return 403, _error("image_not_allowed")
        if attempt_id in self.containers:
            return 200, self._status(attempt_id)
        if self.docker_down:
            return 503, _error("docker_unavailable")
        work_dir = self.work_area / attempt_id
        if not work_dir.is_dir():
            return 409, _error("work_dir_missing")
        others = {entry.name for entry in self.work_area.iterdir()} - {attempt_id}
        if others - _RESERVED_ENTRIES:
            return 409, _error("work_area_dirty")
        process = None
        if self.command is not None:
            process = subprocess.Popen(  # noqa: S603 - comando fixo do teste
                [*self.command, str(work_dir)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
            )
        self.containers[attempt_id] = FakeContainer(process=process, oom_killed=self.oom_on_exit)
        return 200, self._status(attempt_id)

    def _status(self, attempt_id: str) -> dict[str, object]:
        container = self.containers.get(attempt_id)
        if container is None:
            return {
                "state": "absent",
                "exit_code": None,
                "oom_killed": False,
                "started_at": None,
                "finished_at": None,
            }
        return container.as_status()

    def _stop(self, attempt_id: str, body: bytes) -> tuple[int, dict[str, Any] | None]:
        document = _json_object(body)
        grace = None if document is None else document.get("grace_s")
        if not isinstance(grace, int | float) or isinstance(grace, bool) or not 0 <= grace <= 10:
            return 400, _error("invalid_body")
        if self.on_stop is not None:
            self.on_stop(attempt_id)
        container = self.containers.get(attempt_id)
        if container is None or self.unstoppable:
            return 200, self._status(attempt_id)
        container.refresh()
        if container.state != "running":
            return 200, self._status(attempt_id)
        if container.process is None:
            container.state, container.exit_code = "exited", _SIGNAL_EXIT_BASE + signal.SIGTERM
            return 200, self._status(attempt_id)
        _signal_group(container.process, signal.SIGTERM)
        try:
            container.process.wait(timeout=float(grace))
        except subprocess.TimeoutExpired:
            _signal_group(container.process, signal.SIGKILL)
            container.process.wait(timeout=_KILL_CONFIRM_S)
        return 200, self._status(attempt_id)

    def _remove(self, attempt_id: str) -> tuple[int, dict[str, Any] | None]:
        container = self.containers.get(attempt_id)
        if container is not None:
            container.refresh()
            if container.state == "running":
                return 409, _error("still_running")
            del self.containers[attempt_id]
        shutil.rmtree(self.work_area / attempt_id, ignore_errors=True)
        return 204, None


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    # `server_close` espera as requisições em curso: nenhum "container" nasce depois do close.
    daemon_threads = False
    block_on_close = True


def _handler_for(ctl: FakeExecutorCtl) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_DELETE(self) -> None:
            self._dispatch()

        def _dispatch(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            status, document = ctl.handle(self.command, self.path, body)
            payload = b"" if document is None else json.dumps(document).encode()
            # O cliente que desistiu por timeout já fechou a conexão.
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.send_response(status)
                if document is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            # O endereço do cliente de um socket Unix é vazio; o log padrão quebraria.
            return

    return Handler


def _json_object(body: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(body)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _error(code: str) -> dict[str, Any]:
    return {"error": code, "detail": f"falso: {code}"}


def _signal_group(process: subprocess.Popen[bytes], signum: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signum)
