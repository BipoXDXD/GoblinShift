"""HTTP/1.1 mínimo sobre socket Unix (docs/GoblinShiftPlan.md, 3.6, "Protocolo do executor-ctl").

| Operação | Requisição                                      | Sucesso          |
|----------|-------------------------------------------------|------------------|
| start    | POST /v1/attempts/{id}/start {"image": ref}     | 200 Status       |
| status   | GET /v1/attempts/{id}                           | 200 Status       |
| stop     | POST /v1/attempts/{id}/stop {"grace_s": 0..10}  | 200 Status       |
| remove   | DELETE /v1/attempts/{id}                        | 204              |

Erros: `{"error": "<código>", "detail": "..."}`. Uma requisição por conexão, corpo JSON pequeno
com Content-Length obrigatório e prazo de leitura curto: o cliente é o worker, mas um cliente
lento ou com defeito não pode prender o serviço.
"""

import json
import logging
import os
import re
import socketserver
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .config import Config
from .docker_cli import ContainerRunningError, ContainerStatus, DockerUnavailableError
from .images import IMAGE_REFERENCE, ImageNotAllowedError
from .service import MAX_STOP_GRACE_S, ExecutorService
from .workarea import WorkAreaDirtyError, WorkDirMissingError

MAX_BODY_BYTES = 1024
# Corpo não usado (413, 404, 405...) até este tamanho é lido e descartado antes da resposta.
DISCARD_LIMIT_BYTES = 64 * 1024
# Sem Content-Length, quanto esperar pelo que o cliente já mandou antes de responder.
_DRAIN_WAIT_S = 0.05
READ_TIMEOUT_S = 5.0
SOCKET_MODE = 0o660
# Enquanto o socket nasce, só o dono (root) o acessa; o grupo entra depois do chown.
_BIND_UMASK = 0o177

_ROUTE = re.compile(r"/v1/attempts/(?P<attempt>[^/?#]+)(?P<action>/start|/stop)?")
_ERRORS: tuple[tuple[type[Exception], HTTPStatus, str], ...] = (
    (ImageNotAllowedError, HTTPStatus.FORBIDDEN, "image_not_allowed"),
    (WorkAreaDirtyError, HTTPStatus.CONFLICT, "work_area_dirty"),
    (WorkDirMissingError, HTTPStatus.CONFLICT, "work_dir_missing"),
    (ContainerRunningError, HTTPStatus.CONFLICT, "still_running"),
    (DockerUnavailableError, HTTPStatus.SERVICE_UNAVAILABLE, "docker_unavailable"),
)

_log = logging.getLogger(__name__)

Payload = dict[str, object] | None


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code


class _ClientGoneError(Exception):
    """O cliente não mandou o corpo dentro do prazo; não há a quem responder."""


class ControlServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    # stop pode levar a carência inteira; no SIGTERM, server_close espera as requisições em voo.
    daemon_threads = False
    block_on_close = True

    def __init__(self, path: Path, service: ExecutorService, read_timeout_s: float) -> None:
        self.service = service
        self.read_timeout_s = read_timeout_s
        self.socket_path = path
        super().__init__(str(path), ControlHandler)

    def server_close(self) -> None:
        super().server_close()
        _remove_stale_socket(self.socket_path)


def bind_server(
    config: Config,
    service: ExecutorService,
    group_id: int,
    *,
    read_timeout_s: float = READ_TIMEOUT_S,
) -> ControlServer:
    """Cria o socket com dono root, grupo `group_id` e modo 0660."""
    _remove_stale_socket(config.socket)
    previous_umask = os.umask(_BIND_UMASK)
    try:
        server = ControlServer(config.socket, service, read_timeout_s)
    finally:
        os.umask(previous_umask)
    os.chown(config.socket, -1, group_id)
    os.chmod(config.socket, SOCKET_MODE)
    return server


def _remove_stale_socket(path: Path) -> None:
    # Só um socket de execução anterior é apagado; qualquer outro arquivo no caminho é erro.
    if not os.path.lexists(path):
        return
    if not stat.S_ISSOCK(path.lstat().st_mode):
        raise RuntimeError(f"{path} existe e não é um socket")
    path.unlink()


def parse_attempt_id(text: str) -> uuid.UUID:
    """Só a forma canônica (minúscula, com hífens): um id, um nome de container."""
    try:
        attempt_id = uuid.UUID(text)
    except ValueError:
        attempt_id = None
    if attempt_id is None or str(attempt_id) != text:
        raise RequestError(
            HTTPStatus.BAD_REQUEST, "invalid_attempt_id", "attempt_id precisa ser UUID canônico"
        )
    return attempt_id


def parse_start_body(raw: bytes) -> str:
    body = _parse_object(raw, required_keys={"image"})
    image = body["image"]
    if not isinstance(image, str) or not IMAGE_REFERENCE.fullmatch(image):
        raise _invalid("image precisa ser uma referência <nome>@sha256:<64 hex>")
    return image


def parse_stop_body(raw: bytes) -> int:
    body = _parse_object(raw, required_keys={"grace_s"})
    grace_s = body["grace_s"]
    # type() e não isinstance(): True também é int.
    if type(grace_s) is not int or not 0 <= grace_s <= MAX_STOP_GRACE_S:
        raise _invalid(f"grace_s precisa ser inteiro de 0 a {MAX_STOP_GRACE_S}")
    return grace_s


def _parse_object(raw: bytes, *, required_keys: set[str]) -> dict[str, object]:
    try:
        body = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise _invalid(f"corpo não é JSON válido: {error}") from error
    if not isinstance(body, dict):
        raise _invalid("corpo precisa ser um objeto JSON")
    if set(body) != required_keys:
        raise _invalid(f"corpo precisa ter exatamente as chaves {sorted(required_keys)}")
    return body


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("chave duplicada")
    return dict(pairs)


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} não é JSON")


def _invalid(detail: str) -> RequestError:
    return RequestError(HTTPStatus.BAD_REQUEST, "invalid_request", detail)


class ControlHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Sem versão do Python nas respostas.
    server_version = "executor-ctl"
    sys_version = ""
    server: ControlServer

    def setup(self) -> None:
        super().setup()
        self._body_consumed = False
        # Prazo por leitura: vale para a linha de requisição, os cabeçalhos e o corpo.
        self.connection.settimeout(self.server.read_timeout_s)

    def address_string(self) -> str:
        return "unix"

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Erros do próprio http.server (método desconhecido, linha inválida) também em JSON."""
        self._respond(code, {"error": f"http_{code}", "detail": message or HTTPStatus(code).phrase})

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """O log de cada requisição sai de `_dispatch`, com os campos do protocolo."""

    def log_message(self, format: str, *args: object) -> None:
        _log.warning("http.server", extra={"detail": format % args})

    def _dispatch(self, method: str) -> None:
        started = time.monotonic()
        fields: dict[str, object] = {}
        try:
            status, payload = self._handle(method, fields)
        except _ClientGoneError:
            self.close_connection = True
            _log.warning("cliente não mandou o corpo no prazo", extra=fields)
            return
        self._respond(status, payload)
        fields["status"] = int(status)
        fields["duration_ms"] = round((time.monotonic() - started) * 1000)
        if payload is not None and "error" in payload:
            fields["error"] = payload["error"]
        _log.info("request", extra=fields)

    def _handle(self, method: str, fields: dict[str, object]) -> tuple[HTTPStatus, Payload]:
        try:
            route = _ROUTE.fullmatch(self.path)
            if route is None:
                raise RequestError(HTTPStatus.NOT_FOUND, "not_found", "rota desconhecida")
            operation = _OPERATIONS.get((method, route["action"]))
            if operation is None:
                raise RequestError(
                    HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "método não aceito aqui"
                )
            name, run = operation
            fields["operation"] = name
            attempt_id = parse_attempt_id(route["attempt"])
            fields["attempt_id"] = str(attempt_id)
            return run(self, attempt_id, fields)
        except RequestError as error:
            return error.status, {"error": error.code, "detail": str(error)}
        except _ClientGoneError:
            raise
        except Exception as error:
            return _error_payload(error)

    def _start(
        self, attempt_id: uuid.UUID, fields: dict[str, object]
    ) -> tuple[HTTPStatus, Payload]:
        image = parse_start_body(self._read_body())
        fields["image"] = image
        return HTTPStatus.OK, self.server.service.start(attempt_id, image).to_json()

    def _status(
        self, attempt_id: uuid.UUID, fields: dict[str, object]
    ) -> tuple[HTTPStatus, Payload]:
        return HTTPStatus.OK, self.server.service.status(attempt_id).to_json()

    def _stop(self, attempt_id: uuid.UUID, fields: dict[str, object]) -> tuple[HTTPStatus, Payload]:
        grace_s = parse_stop_body(self._read_body())
        status: ContainerStatus = self.server.service.stop(attempt_id, grace_s)
        return HTTPStatus.OK, status.to_json()

    def _remove(
        self, attempt_id: uuid.UUID, fields: dict[str, object]
    ) -> tuple[HTTPStatus, Payload]:
        self.server.service.remove(attempt_id)
        return HTTPStatus.NO_CONTENT, None

    def _read_body(self) -> bytes:
        length_header = self.headers.get("Content-Length")
        if length_header is None or "Transfer-Encoding" in self.headers:
            raise RequestError(
                HTTPStatus.LENGTH_REQUIRED, "length_required", "Content-Length obrigatório"
            )
        if not length_header.isdigit():
            raise _invalid("Content-Length inválido")
        length = int(length_header)
        if length > MAX_BODY_BYTES:
            raise RequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "body_too_large",
                f"corpo acima de {MAX_BODY_BYTES} bytes",
            )
        try:
            body = self.rfile.read(length)
        except TimeoutError as error:
            raise _ClientGoneError from error
        if len(body) != length:
            raise _ClientGoneError
        self._body_consumed = True
        return body

    def _discard_unread_body(self) -> None:
        """Lê e descarta o corpo que a resposta não usou (erro antes de chegar a ele).

        Fechar o socket com bytes não lidos faz o cliente receber reset em vez da resposta.
        Melhor esforço: corpo acima do limite ou lento fica sem descarte; sem Content-Length
        (chunked), descarta só o que já chegou.
        """
        headers = getattr(self, "headers", None)
        if self._body_consumed or headers is None:
            return
        self._body_consumed = True
        length = headers.get("Content-Length", "")
        if length.isdigit():
            if int(length) <= DISCARD_LIMIT_BYTES:
                with suppress(TimeoutError):
                    self.rfile.read(int(length))
            return
        self.connection.settimeout(_DRAIN_WAIT_S)
        with suppress(TimeoutError):
            self.rfile.read1(DISCARD_LIMIT_BYTES)

    def _respond(self, status: int, payload: Payload) -> None:
        # Uma requisição por conexão: sem keep-alive, nada sobra no buffer para a próxima.
        self.close_connection = True
        self._discard_unread_body()
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Connection", "close")
        if status != HTTPStatus.NO_CONTENT:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _error_payload(error: Exception) -> tuple[HTTPStatus, Payload]:
    for error_type, status, code in _ERRORS:
        if isinstance(error, error_type):
            return status, {"error": code, "detail": str(error)}
    # Defeito do próprio executor-ctl: o detalhe fica no journal, não na resposta.
    _log.exception("erro inesperado")
    return HTTPStatus.INTERNAL_SERVER_ERROR, {
        "error": "internal_error",
        "detail": "erro interno do executor-ctl; veja o journal",
    }


_Operation = Callable[[ControlHandler, uuid.UUID, dict[str, object]], tuple[HTTPStatus, Payload]]
_OPERATIONS: dict[tuple[str, str | None], tuple[str, _Operation]] = {
    ("POST", "/start"): ("start", ControlHandler._start),
    ("GET", None): ("status", ControlHandler._status),
    ("POST", "/stop"): ("stop", ControlHandler._stop),
    ("DELETE", None): ("remove", ControlHandler._remove),
}
