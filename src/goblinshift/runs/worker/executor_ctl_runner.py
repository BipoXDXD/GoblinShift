"""Runner de produção: cliente do `executor-ctl` (docs/GoblinShiftPlan.md, 3.2 e 3.6).

O `executor-ctl` é o serviço do host, único com acesso ao Docker, que cria um container gVisor
por tentativa com perfil fixo. O worker fala com ele por HTTP/1.1 num socket Unix
(`/run/goblinshift/executor-ctl.sock`, grupo `gs-exec`), só com a biblioteca padrão, e nunca
escolhe montagens nem opções: só manda o `attempt_id` e a imagem fixada na Run.

Uma conexão por chamada, com timeout de 10 s (30 s no `start`, que cria o container). Qualquer
falha de transporte (socket ausente, recusa, timeout, resposta ilegível ou fora do contrato) vira
`ExecutorCtlError` com código `unavailable` ou `invalid_response`; recusas do serviço trazem o
código do corpo (`image_not_allowed`, `work_area_dirty`, `still_running`…). Para quem chama, tudo
é `RunnerError`: o coordenador encerra a tentativa como falha transitória, e a manutenção não
toca no que não consegue inspecionar.

A área de trabalho (`<work_root>/<attempt_id>`) é criada pelo worker, que pertence ao grupo do
executor (GID 61001), com modo 2770; o `remove` do serviço a apaga inteira, inclusive o que o
executor (outro usuário) escreveu.
"""

import http.client
import json
import math
import socket
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from goblinshift.runs.worker.runner import (
    MAX_STOP_GRACE,
    ExecutorPhase,
    ExecutorStatus,
    RunnerError,
    create_work_layout,
    parse_attempt_id,
)

# 3.2: 10 s nas chamadas ao `executor-ctl` e 30 s no `start`.
CALL_TIMEOUT = timedelta(seconds=10)
START_TIMEOUT = timedelta(seconds=30)
# Respostas do contrato são objetos pequenos; acima disso, o serviço está fora do contrato.
MAX_RESPONSE_BYTES = 64 * 1024
UNAVAILABLE = "unavailable"
INVALID_RESPONSE = "invalid_response"

_PHASES = {
    "absent": ExecutorPhase.ABSENT,
    # Criado e ainda não iniciado pode virar `running` a qualquer momento: só o fim confirmado
    # (exited ou absent) libera reagendar ou remover.
    "created": ExecutorPhase.RUNNING,
    "running": ExecutorPhase.RUNNING,
    "exited": ExecutorPhase.EXITED,
}


class ExecutorCtlError(RunnerError):
    """O `executor-ctl` não cumpriu a operação; `code` é o motivo do contrato."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"executor-ctl: {code}: {detail}")
        self.code = code


class ExecutorCtlRunner:
    def __init__(
        self,
        socket_path: Path,
        work_root: Path,
        *,
        work_group: int,
        call_timeout: timedelta = CALL_TIMEOUT,
        start_timeout: timedelta = START_TIMEOUT,
    ) -> None:
        self._socket_path = socket_path
        self._root = work_root
        self._group = work_group
        self._call_timeout = call_timeout
        self._start_timeout = start_timeout

    def work_dir(self, attempt_id: uuid.UUID) -> Path:
        return self._root / str(attempt_id)

    def create_work_dir(self, attempt_id: uuid.UUID) -> Path:
        work_dir = self.work_dir(attempt_id)
        create_work_layout(work_dir, group=self._group)
        return work_dir

    def list_work(self) -> list[uuid.UUID]:
        names = (entry.name for entry in self._root.iterdir() if entry.is_dir())
        return sorted(
            {attempt_id for name in names if (attempt_id := parse_attempt_id(name)) is not None}
        )

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        return self._status_call(
            "POST", f"{_path(attempt_id)}/start", {"image": image}, self._start_timeout
        )

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        return self._status_call("GET", _path(attempt_id), None, self._call_timeout)

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        # Segundos inteiros (0 a 10), arredondados para cima: nunca menos carência que a pedida.
        grace_s = math.ceil(min(grace, MAX_STOP_GRACE).total_seconds())
        # O `stop` só responde depois de SIGTERM, carência e SIGKILL: o timeout soma a carência.
        return self._status_call(
            "POST",
            f"{_path(attempt_id)}/stop",
            {"grace_s": grace_s},
            self._call_timeout + timedelta(seconds=grace_s),
        )

    def remove(self, attempt_id: uuid.UUID) -> None:
        self._request("DELETE", _path(attempt_id), None, self._call_timeout)

    def _status_call(
        self, method: str, path: str, body: dict[str, object] | None, timeout: timedelta
    ) -> ExecutorStatus:
        document = self._request(method, path, body, timeout)
        if document is None:
            raise ExecutorCtlError(INVALID_RESPONSE, f"{method} {path} sem corpo")
        return _parse_status(document)

    def _request(
        self, method: str, path: str, body: dict[str, object] | None, timeout: timedelta
    ) -> dict[str, Any] | None:
        """Uma requisição numa conexão nova; devolve o corpo JSON (ou `None` no 204)."""
        payload = None if body is None else json.dumps(body).encode()
        headers = {} if payload is None else {"Content-Type": "application/json"}
        connection = _UnixConnection(self._socket_path, timeout.total_seconds())
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException) as error:
            raise ExecutorCtlError(UNAVAILABLE, f"{method} {path}: {error!r}") from error
        finally:
            connection.close()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ExecutorCtlError(INVALID_RESPONSE, f"{method} {path}: resposta grande demais")
        if response.status == http.HTTPStatus.NO_CONTENT:
            return None
        document = _json_object(raw)
        if response.status == http.HTTPStatus.OK and document is not None:
            return document
        raise ExecutorCtlError(
            _error_code(response.status, document), f"{method} {path} respondeu {response.status}"
        )


class _UnixConnection(http.client.HTTPConnection):
    """`HTTPConnection` que conecta a um socket Unix em vez de host e porta."""

    def __init__(self, socket_path: Path, timeout_s: float) -> None:
        # O host só vai no cabeçalho Host; a conexão real é o socket.
        super().__init__("localhost", timeout=timeout_s)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self._socket_path))
        except OSError:
            sock.close()
            raise
        self.sock = sock


def _path(attempt_id: uuid.UUID) -> str:
    return f"/v1/attempts/{attempt_id}"


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _error_code(status: int, document: dict[str, Any] | None) -> str:
    """Código do corpo de erro; sem ele, 5xx é indisponibilidade e o resto, fora do contrato."""
    code = None if document is None else document.get("error")
    if isinstance(code, str):
        return code
    if status >= http.HTTPStatus.INTERNAL_SERVER_ERROR:
        return UNAVAILABLE
    return INVALID_RESPONSE


def _parse_status(document: dict[str, Any]) -> ExecutorStatus:
    state = document.get("state")
    phase = _PHASES.get(state) if isinstance(state, str) else None
    exit_code = document.get("exit_code")
    oom_killed = document.get("oom_killed")
    valid_exit_code = exit_code is None or (
        isinstance(exit_code, int) and not isinstance(exit_code, bool)
    )
    if phase is None or not valid_exit_code or not isinstance(oom_killed, bool):
        raise ExecutorCtlError(INVALID_RESPONSE, f"Status fora do contrato: {document!r}")
    return ExecutorStatus(phase, exit_code=exit_code, oom_killed=oom_killed)
