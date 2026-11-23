"""Protocolo HTTP do executor-ctl sem Docker: tudo aqui é recusado antes de chamar o CLI."""

import http.client
import importlib
import itertools
import json
import os
import socket as socketlib
import stat
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import quote

import pytest

from executor_ctl import profile
from executor_ctl.__main__ import EXIT_HOST_NOT_READY
from executor_ctl.client import UnixHTTPConnection, call
from executor_ctl.docker_cli import DockerCli
from executor_ctl.journal import JsonFormatter
from executor_ctl.server import (
    MAX_BODY_BYTES,
    MAX_CONNECTIONS,
    REQUEST_READ_DEADLINE_S,
    ControlHandler,
    ControlServer,
    _error_payload,
    bind_server,
)
from executor_ctl.service import ExecutorService

from .conftest import TEST_READ_TIMEOUT_S, make_config, running_server

pytestmark = pytest.mark.host_python

ATTEMPT = "0193a3f0-7b2c-7def-8a01-23456789abcd"
ALLOWED = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
OTHER_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "f" * 64
# Texto de quem fala com o socket: não pode aparecer em nenhuma linha do journal.
CANARY = f"CANARY-{uuid.uuid4()}"


@pytest.fixture
def ctl_socket(tmp_path: Path, socket_dir: Path) -> Iterator[Path]:
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])
    # Um docker que não existe: se algum caso chegasse ao CLI, a resposta seria 503.
    with running_server(config, DockerCli(binary=str(tmp_path / "sem-docker"))) as socket:
        yield socket


def raw_request(socket: Path, method: str, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
    connection = UnixHTTPConnection(socket, timeout_s=5)
    try:
        connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        parsed = json.loads(response.read())
    finally:
        connection.close()
    assert isinstance(parsed, dict)
    return response.status, parsed


def test_socket_fica_com_modo_0660(ctl_socket: Path) -> None:
    mode = ctl_socket.stat().st_mode

    assert stat.S_ISSOCK(mode)
    assert stat.S_IMODE(mode) == 0o660


@pytest.mark.parametrize(
    "attempt_id",
    [
        "nao-e-uuid",
        ATTEMPT.upper(),
        ATTEMPT.replace("-", ""),
        "{" + ATTEMPT + "}",
        "urn:uuid:" + ATTEMPT,
        "..",
    ],
)
def test_attempt_id_que_nao_e_uuid_canonico_da_400(ctl_socket: Path, attempt_id: str) -> None:
    response = call(ctl_socket, "GET", f"/v1/attempts/{attempt_id}")

    assert response.status == 400
    assert response.body == {"error": "invalid_attempt_id", "detail": response.body["detail"]}


@pytest.mark.parametrize(
    "path",
    [
        f"/v1/attempts/{ATTEMPT}/logs",
        f"/v1/attempts/{ATTEMPT}?x=1",
        "/",
        "/v1/attempts/",
        "/v1/image",
        "/v1/image?",
        "/v1/image?x=1",
        f"/v1/image/{ALLOWED}",
        f"/v1/images?ref={ALLOWED}",
    ],
)
def test_rota_desconhecida_da_404(ctl_socket: Path, path: str) -> None:
    response = call(ctl_socket, "GET", path)

    assert response.status == 404
    assert response.body["error"] == "not_found"


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", f"/v1/attempts/{ATTEMPT}/start"), ("POST", f"/v1/attempts/{ATTEMPT}")],
)
def test_metodo_errado_na_rota_da_405(ctl_socket: Path, method: str, path: str) -> None:
    response = call(ctl_socket, method, path, {})

    assert response.status == 405
    assert response.body["error"] == "method_not_allowed"


def test_metodo_desconhecido_da_erro_em_json(ctl_socket: Path) -> None:
    status, body = raw_request(ctl_socket, "PATCH", f"/v1/attempts/{ATTEMPT}", b"{}")

    assert status == 501
    assert set(body) == {"error", "detail"}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"{}", "sem image"),
        (json.dumps({"image": ALLOWED, "runtime": "runc"}).encode(), "chave a mais"),
        (json.dumps({"image": ALLOWED, "mounts": ["/:/host"]}).encode(), "montagem do cliente"),
        (json.dumps({"image": "python:3.14"}).encode(), "imagem sem digest"),
        (json.dumps({"image": ALLOWED + " --privileged"}).encode(), "opção embutida"),
        (json.dumps({"image": 7}).encode(), "tipo errado"),
        (json.dumps([ALLOWED]).encode(), "não é objeto"),
        (b'{"image": "x", "image": "y"}', "chave duplicada"),
        (b'{"image": NaN}', "constante não-JSON"),
        (b"{image}", "JSON inválido"),
        (b"\xff\xfe", "não é UTF-8"),
        (b"", "corpo vazio"),
    ],
)
def test_corpo_invalido_no_start_da_400(ctl_socket: Path, body: bytes, reason: str) -> None:
    status, response = raw_request(ctl_socket, "POST", f"/v1/attempts/{ATTEMPT}/start", body)

    assert status == 400
    assert response == {"error": "invalid_request", "detail": response["detail"]}


def test_corpo_acima_do_limite_da_413(ctl_socket: Path) -> None:
    body = json.dumps({"image": "a" * MAX_BODY_BYTES}).encode()

    status, response = raw_request(ctl_socket, "POST", f"/v1/attempts/{ATTEMPT}/start", body)

    assert status == 413
    assert response["error"] == "body_too_large"


def test_corpo_sem_content_length_da_411(ctl_socket: Path) -> None:
    connection = UnixHTTPConnection(ctl_socket, timeout_s=5)
    try:
        connection.putrequest("POST", f"/v1/attempts/{ATTEMPT}/start")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.endheaders()
        connection.send(b"0\r\n\r\n")
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
    finally:
        connection.close()

    assert status == 411
    assert body["error"] == "length_required"


def raw_answer(socket: Path, request: bytes) -> bytes:
    with socketlib.socket(socketlib.AF_UNIX) as raw:
        raw.settimeout(5)
        raw.connect(str(socket))
        raw.sendall(request)
        # A resposta traz `Connection: close`, e o servidor escreve cabeçalhos e corpo em
        # chamadas separadas: um `recv` só pode voltar sem o corpo. Lê até o EOF.
        chunks = []
        while chunk := raw.recv(4096):
            chunks.append(chunk)
        return b"".join(chunks)


# Mais dígitos que o limite de conversão do `int()` (4300) também derrubava a conexão.
TOO_MANY_DIGITS = b"9" * 5000


@pytest.mark.parametrize(
    "length",
    [
        b"\xb2",
        b"\xb9",
        b"1\xb2",
        b"-1",
        b"+5",
        b"1_0",
        b"",
        b"1 0",
    ],
)
def test_content_length_que_nao_e_so_digito_ascii_da_400_no_start(
    ctl_socket: Path, length: bytes
) -> None:
    # `str.isdigit()` aceita o "²" (U+00B2) que o `http.server` decodifica de um byte 0xB2, mas
    # `int()` o recusa: sem a conferência de ASCII, a recusa virava 500 `internal_error`.
    request = (
        f"POST /v1/attempts/{ATTEMPT}/start HTTP/1.1\r\nHost: x\r\nContent-Length: ".encode()
        + length
        + b"\r\n\r\n{}"
    )

    answer = raw_answer(ctl_socket, request)

    assert answer.startswith(b"HTTP/1.1 400")
    assert b'"invalid_request"' in answer


@pytest.mark.parametrize(
    "length",
    [
        pytest.param(b"99999", id="maximo-de-digitos"),
        pytest.param(b"1" + b"0" * 5, id="6-digitos"),
        pytest.param(TOO_MANY_DIGITS, id="5000-digitos"),
    ],
)
def test_content_length_bem_formado_acima_do_limite_da_413_no_start(
    ctl_socket: Path, length: bytes
) -> None:
    # Só dígitos ASCII e, sem zeros à esquerda, mais dígitos que o teto: é necessariamente
    # maior que o limite, e o `int()` nunca recebe a string (acima de 4300 dígitos ele lança).
    request = (
        f"POST /v1/attempts/{ATTEMPT}/start HTTP/1.1\r\nHost: x\r\nContent-Length: ".encode()
        + length
        + b"\r\n\r\n"
    )

    answer = raw_answer(ctl_socket, request)

    assert answer.startswith(b"HTTP/1.1 413")
    assert b'"body_too_large"' in answer


def test_content_length_com_zeros_a_esquerda_e_aceito(ctl_socket: Path) -> None:
    # RFC 9110 8.6: `Content-Length = 1*DIGIT`, então "000001" vale 1. O corpo de 1 byte segue
    # o mesmo fluxo que com "1": passa do cabeçalho e é recusado só como JSON que não é objeto.
    def start_with(length: bytes) -> bytes:
        request = (
            f"POST /v1/attempts/{ATTEMPT}/start HTTP/1.1\r\nHost: x\r\nContent-Length: ".encode()
            + length
            + b"\r\n\r\n1"
        )
        return raw_answer(ctl_socket, request)

    answer = start_with(b"000001")

    assert answer == start_with(b"1")
    assert answer.startswith(b"HTTP/1.1 400")
    assert b"Content-Length" not in answer.partition(b"\r\n\r\n")[2]


def test_content_length_nao_ascii_em_rota_que_nao_le_corpo_nao_derruba_a_resposta(
    ctl_socket: Path,
) -> None:
    # O descarte do corpo não lido também usa o `Content-Length`: a resposta de erro da rota
    # precisa chegar ao cliente em vez de a conexão cair no `ValueError` do `int()`.
    request = f"GET /v1/attempts/{ATTEMPT} HTTP/1.1\r\nHost: x\r\nContent-Length: ".encode()

    answer = raw_answer(ctl_socket, request + b"\xb2\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 ")


def test_content_length_com_digitos_demais_em_rota_que_nao_le_corpo_nao_derruba_a_resposta(
    ctl_socket: Path,
) -> None:
    request = f"GET /v1/attempts/{ATTEMPT} HTTP/1.1\r\nHost: x\r\nContent-Length: ".encode()

    # O GET não lê corpo: um valor bem formado acima do teto de dígitos cai no descarte, que
    # desiste sem drenar, e a rota responde como responderia sem o cabeçalho.
    answer = raw_answer(ctl_socket, request + TOO_MANY_DIGITS + b"\r\n\r\n")
    plain = raw_answer(
        ctl_socket, f"GET /v1/attempts/{ATTEMPT} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    )

    assert answer.split(b"\r\n", 1)[0] == plain.split(b"\r\n", 1)[0]
    assert answer.partition(b"\r\n\r\n")[2] == plain.partition(b"\r\n\r\n")[2]


def test_imagem_fora_da_allowlist_da_403(ctl_socket: Path) -> None:
    response = call(ctl_socket, "POST", f"/v1/attempts/{ATTEMPT}/start", {"image": OTHER_IMAGE})

    assert response.status == 403
    assert response.body["error"] == "image_not_allowed"


@pytest.mark.parametrize(
    "body",
    [{}, {"grace_s": 11}, {"grace_s": -1}, {"grace_s": True}, {"grace_s": "5"}, {"grace_s": 1.5}],
)
def test_carencia_invalida_no_stop_da_400(ctl_socket: Path, body: dict[str, object]) -> None:
    response = call(ctl_socket, "POST", f"/v1/attempts/{ATTEMPT}/stop", body)

    assert response.status == 400
    assert response.body["error"] == "invalid_request"


def test_docker_indisponivel_da_503_sem_detalhe_interno(ctl_socket: Path) -> None:
    response = call(ctl_socket, "GET", f"/v1/attempts/{uuid.uuid4()}")

    assert response.status == 503
    assert response.body["error"] == "docker_unavailable"
    assert "Traceback" not in json.dumps(response.body)


def test_cliente_lento_e_desconectado_sem_derrubar_o_servidor(ctl_socket: Path) -> None:
    stalled = UnixHTTPConnection(ctl_socket, timeout_s=10)
    try:
        stalled.putrequest("POST", f"/v1/attempts/{ATTEMPT}/start")
        stalled.putheader("Content-Length", "100")
        stalled.endheaders()
        # Não manda o corpo; outro cliente continua sendo atendido enquanto este espera.
        response = call(ctl_socket, "GET", "/v1/attempts/x")
        assert response.status == 400
        with pytest.raises((http.client.RemoteDisconnected, ConnectionResetError)):
            stalled.getresponse()
    finally:
        stalled.close()


def image_path(ref: str) -> str:
    return f"/v1/image?ref={ref}"


def recording_docker(
    tmp_path: Path, stderr: str = "", exit_code: int = 0
) -> tuple[DockerCli, Path]:
    """Um "docker" que grava os argumentos: prova o que chegou (ou não) ao CLI."""
    calls = tmp_path / "docker-calls.jsonl"
    script = tmp_path / "docker-gravador"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"with open({str(calls)!r}, 'a') as file:\n"
        "    file.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n"
    )
    script.chmod(0o755)
    return DockerCli(binary=str(script)), calls


@pytest.mark.usefixtures("synthetic_policy_metadata")
@pytest.mark.parametrize(
    ("stderr", "exit_code", "present"),
    [
        ("", 0, True),
        ("Error response from daemon: No such image: x\n", 1, False),
    ],
)
def test_sonda_responde_200_com_so_o_booleano_presente_ou_ausente(
    tmp_path: Path, socket_dir: Path, stderr: str, exit_code: int, present: bool
) -> None:
    docker, calls = recording_docker(tmp_path, stderr, exit_code)
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])

    with running_server(config, docker) as socket:
        response = call(socket, "GET", image_path(ALLOWED))

    assert response.status == 200
    assert response.body == {"present": present}
    assert json.loads(calls.read_text()) == ["image", "inspect", "--format={{.Id}}", ALLOWED]


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_sonda_aceita_a_ref_codificada_e_entrega_ao_docker_a_ref_decodificada(
    tmp_path: Path, socket_dir: Path
) -> None:
    docker, calls = recording_docker(tmp_path)
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])

    with running_server(config, docker) as socket:
        response = call(socket, "GET", image_path(quote(ALLOWED, safe="")))

    assert response.status == 200
    assert response.body == {"present": True}
    assert json.loads(calls.read_text()) == ["image", "inspect", "--format={{.Id}}", ALLOWED]


@pytest.mark.usefixtures("synthetic_policy_metadata")
@pytest.mark.parametrize(
    "ref",
    [
        OTHER_IMAGE,
        "ghcr.io/outro-projeto/app@sha256:" + "a" * 64,
        "postgres@sha256:" + "b" * 64,
    ],
    ids=["mesmo-repositorio", "outro-projeto", "imagem-publica"],
)
def test_sonda_de_ref_fora_da_allowlist_da_403_sem_chegar_ao_docker(
    tmp_path: Path, socket_dir: Path, ref: str
) -> None:
    # O "docker" responde 0: se a allowlist não barrasse, a resposta seria 200 com present=true.
    docker, calls = recording_docker(tmp_path)
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])

    with running_server(config, docker) as socket:
        response = call(socket, "GET", image_path(ref))

    assert response.status == 403
    assert response.body == {"error": "image_not_allowed", "detail": response.body["detail"]}
    assert not calls.exists()


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_sonda_com_docker_ausente_da_503_e_nunca_diz_ausente(
    tmp_path: Path, socket_dir: Path
) -> None:
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])

    with running_server(config, DockerCli(binary=str(tmp_path / "sem-docker"))) as socket:
        response = call(socket, "GET", image_path(ALLOWED))

    assert response.status == 503
    assert response.body == {"error": "docker_unavailable", "detail": response.body["detail"]}
    assert "Traceback" not in json.dumps(response.body)


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "python:3.14",
        ALLOWED[:-1],
        ALLOWED + "0",
        ALLOWED.upper(),
        "--help",
        "-x@sha256:" + "a" * 64,
        ALLOWED + "&ref=" + OTHER_IMAGE,
        ALLOWED + "#x",
        ALLOWED + "%20--privileged",
        quote(ALLOWED + "&ref=" + OTHER_IMAGE, safe=""),
        ALLOWED.replace("@", "%2540"),
    ],
)
def test_sonda_com_ref_malformada_da_400_sem_devolver_a_ref(ctl_socket: Path, ref: str) -> None:
    response = call(ctl_socket, "GET", image_path(ref))

    assert response.status == 400
    assert response.body == {"error": "invalid_request", "detail": response.body["detail"]}
    assert not ref or ref not in response.body["detail"]


def test_sonda_com_ref_com_nul_da_400(ctl_socket: Path) -> None:
    # O cliente http.client recusa byte de controle na URL; aqui ele vai cru pelo socket.
    with socketlib.socket(socketlib.AF_UNIX) as raw:
        raw.settimeout(5)
        raw.connect(str(ctl_socket))
        raw.sendall(b"GET /v1/image?ref=a\x00b HTTP/1.1\r\nHost: x\r\n\r\n")
        answer = raw.recv(4096)

    assert answer.startswith(b"HTTP/1.1 400")


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_sonda_so_aceita_get(ctl_socket: Path, method: str) -> None:
    response = call(ctl_socket, method, image_path(ALLOWED), {})

    assert response.status == 405
    assert response.body["error"] == "method_not_allowed"


# Zeros à esquerda não mudam o valor, mas contam para o limite de dígitos do `int()`.
LEADING_ZEROS = b"0" * 5000


def status_and_body(answer: bytes) -> tuple[bytes, bytes]:
    head, _, body = answer.partition(b"\r\n\r\n")
    return head.split(b"\r\n", 1)[0], body


@pytest.mark.parametrize(
    ("length", "same_as", "body"),
    [
        pytest.param(b"0", b"0", b"", id="zero"),
        pytest.param(b"00000", b"0", b"", id="zeros"),
        pytest.param(LEADING_ZEROS, b"0", b"", id="5000-zeros"),
        pytest.param(LEADING_ZEROS + b"1", b"1", b"1", id="5000-zeros-e-1"),
    ],
)
def test_content_length_so_com_zeros_a_esquerda_vale_o_numero_no_start(
    ctl_socket: Path, length: bytes, same_as: bytes, body: bytes
) -> None:
    def start_with(value: bytes) -> bytes:
        request = (
            f"POST /v1/attempts/{ATTEMPT}/start HTTP/1.1\r\nHost: x\r\nContent-Length: ".encode()
            + value
            + b"\r\n\r\n"
            + body
        )
        return raw_answer(ctl_socket, request)

    answer = start_with(length)

    assert answer.startswith(b"HTTP/1.1 400")
    assert b'"invalid_request"' in answer
    assert status_and_body(answer) == status_and_body(start_with(same_as))


@pytest.mark.parametrize(
    "length",
    [
        pytest.param(b"0", id="zero"),
        pytest.param(b"00000", id="zeros"),
        pytest.param(LEADING_ZEROS, id="5000-zeros"),
        pytest.param(LEADING_ZEROS + b"1", id="5000-zeros-e-1"),
    ],
)
def test_content_length_com_zeros_a_esquerda_em_rota_que_nao_le_corpo_responde_igual(
    ctl_socket: Path, length: bytes
) -> None:
    request = f"GET /v1/attempts/{ATTEMPT} HTTP/1.1\r\nHost: x\r\nContent-Length: ".encode()
    tail = b"1" if length.endswith(b"1") else b""

    answer = raw_answer(ctl_socket, request + length + b"\r\n\r\n" + tail)
    plain = raw_answer(
        ctl_socket, f"GET /v1/attempts/{ATTEMPT} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    )

    assert answer.startswith(b"HTTP/1.1 ")
    assert status_and_body(answer) == status_and_body(plain)


def test_log_message_do_http_server_nao_interpola_os_argumentos(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ControlHandler.log_message(cast(Any, object()), "code %d, message %s", 400, CANARY)

    [record] = [r for r in caplog.records if r.getMessage() == "http.server"]
    assert record.__dict__["detail"] == "code %d, message %s"
    assert CANARY not in JsonFormatter().format(record)


def _raise_with_canary_everywhere() -> None:
    """Mensagem, nota, causa, contexto e membro de grupo, todos com o canário."""
    try:
        try:
            raise ValueError(f"contexto {CANARY}")
        except ValueError:
            # Sem `from`: o ValueError fica no `__context__` do KeyError.
            raise KeyError(f"causa {CANARY}")  # noqa: B904
    except KeyError as cause:
        group = ExceptionGroup(f"grupo {CANARY}", [OSError(f"membro {CANARY}")])
        group.add_note(f"nota {CANARY}")
        raise group from cause


def test_excecao_inesperada_vai_ao_journal_so_com_tipos_e_frames(
    caplog: pytest.LogCaptureFixture,
) -> None:
    try:
        _raise_with_canary_everywhere()
    except ExceptionGroup as error:
        _error_payload(error)

    [record] = [r for r in caplog.records if r.getMessage() == "erro inesperado"]
    line = JsonFormatter().format(record)
    assert CANARY not in line
    exception = json.loads(line)["exception"]
    assert exception.startswith("Traceback (most recent call last):")
    assert "_raise_with_canary_everywhere" in exception
    for kind in ("ExceptionGroup", "KeyError", "ValueError", "OSError"):
        assert f"builtins.{kind}" in exception
    for label in ("[cause]", "[context]", "[group member]"):
        assert label in exception


def test_excecao_que_escapa_do_handler_vai_ao_journal_so_com_tipos_e_frames(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    # O padrão do socketserver imprime o traceback inteiro no stderr, que também vai ao journal.
    try:
        raise BrokenPipeError(f"pipe {CANARY}")
    except BrokenPipeError:
        ControlServer.handle_error(cast(Any, object()), cast(Any, object()), "")

    assert CANARY not in capsys.readouterr().err
    [record] = [r for r in caplog.records if r.getMessage() == "erro no handler"]
    line = JsonFormatter().format(record)
    assert CANARY not in line
    assert "builtins.BrokenPipeError" in json.loads(line)["exception"]


# Guardas de falha: só estouram se o comportamento testado não acontecer.
ANSWER_GUARD_S = 5.0
DRIP_GUARD_S = 10.0
_RETRY_PACE_S = 0.01


@contextmanager
def serving(
    tmp_path: Path,
    socket_dir: Path,
    *,
    read_timeout_s: float = TEST_READ_TIMEOUT_S,
    max_connections: int = MAX_CONNECTIONS,
    read_deadline_s: float = REQUEST_READ_DEADLINE_S,
) -> Iterator[Path]:
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])
    with running_server(
        config,
        DockerCli(binary=str(tmp_path / "sem-docker")),
        read_timeout_s=read_timeout_s,
        max_connections=max_connections,
        read_deadline_s=read_deadline_s,
    ) as socket:
        yield socket


def open_partial_request(socket: Path, prefix: bytes) -> socketlib.socket:
    """Conexão que manda o começo da requisição e fica parada, segurando uma vaga."""
    raw = socketlib.socket(socketlib.AF_UNIX)
    raw.connect(str(socket))
    raw.sendall(prefix)
    return raw


def read_until_eof(raw: socketlib.socket) -> bytes:
    chunks = []
    while chunk := raw.recv(4096):
        chunks.append(chunk)
    return b"".join(chunks)


def parse_answer(answer: bytes) -> tuple[int, dict[str, Any]]:
    head, _, body = answer.partition(b"\r\n\r\n")
    parsed = json.loads(body)
    assert isinstance(parsed, dict)
    return int(head.split(b" ", 2)[1]), parsed


def request_until_answered(socket: Path, method: str, path: str) -> tuple[int, dict[str, Any]]:
    """Repete a conexão até vir uma resposta: a recusa pode fechar antes de ler o pedido."""
    request = f"{method} {path} HTTP/1.1\r\nContent-Length: 0\r\n\r\n".encode()
    deadline = time.monotonic() + ANSWER_GUARD_S
    while True:
        try:
            with socketlib.socket(socketlib.AF_UNIX) as raw:
                raw.settimeout(ANSWER_GUARD_S)
                raw.connect(str(socket))
                raw.sendall(request)
                answer = read_until_eof(raw)
        except ConnectionError:
            answer = b""
        if answer or time.monotonic() > deadline:
            return parse_answer(answer)
        threading.Event().wait(_RETRY_PACE_S)


def request_until_not_busy(socket: Path, method: str, path: str) -> tuple[int, dict[str, Any]]:
    """A vaga volta quando a thread da conexão fechada termina, sem aviso ao cliente."""
    deadline = time.monotonic() + ANSWER_GUARD_S
    while True:
        status, body = request_until_answered(socket, method, path)
        if body.get("error") != "busy" or time.monotonic() > deadline:
            return status, body
        threading.Event().wait(_RETRY_PACE_S)


def drip_until_closed(socket: Path, *, every_s: float) -> float:
    """Manda a requisição um byte por vez e devolve quanto o servidor levou para cortar."""
    pace = threading.Event()
    started = time.monotonic()
    with socketlib.socket(socketlib.AF_UNIX) as raw:
        raw.connect(str(socket))
        for byte in itertools.cycle(b"GET /v1/attempts/"):
            elapsed = time.monotonic() - started
            if elapsed > DRIP_GUARD_S:
                return elapsed
            try:
                raw.sendall(bytes([byte]))
                if raw.recv(1, socketlib.MSG_DONTWAIT) == b"":
                    return time.monotonic() - started
            except BlockingIOError:
                pass
            except ConnectionError:
                return time.monotonic() - started
            pace.wait(every_s)
    raise AssertionError("inalcançável: o ciclo de bytes não termina")


def test_conexao_acima_do_teto_recebe_503_busy(tmp_path: Path, socket_dir: Path) -> None:
    with (
        serving(tmp_path, socket_dir, max_connections=2, read_timeout_s=60) as path,
        ExitStack() as idle,
    ):
        for _ in range(2):
            idle.enter_context(open_partial_request(path, b"GET /v1/"))

        status, body = request_until_answered(path, "GET", f"/v1/attempts/{ATTEMPT}")

    assert (status, body["error"]) == (503, "busy")


def test_cliente_gotejando_e_desconectado_no_prazo_total(tmp_path: Path, socket_dir: Path) -> None:
    with serving(tmp_path, socket_dir, read_timeout_s=5, read_deadline_s=0.3) as path:
        closed_after = drip_until_closed(path, every_s=0.05)

    # Cada byte chega bem antes do prazo por leitura (5 s): quem cortou foi o prazo total.
    assert closed_after < 5


def test_requisicao_normal_continua_respondendo_depois_do_teto_liberar(
    tmp_path: Path, socket_dir: Path
) -> None:
    with serving(tmp_path, socket_dir, max_connections=1, read_timeout_s=60) as path:
        with open_partial_request(path, b"GET /v1/"):
            _, refused = request_until_answered(path, "GET", "/v1/attempts/x")

        status, body = request_until_not_busy(path, "GET", "/v1/attempts/x")

    assert refused["error"] == "busy"
    assert (status, body["error"]) == (400, "invalid_attempt_id")


def _fail_to_start(self: threading.Thread) -> None:
    raise RuntimeError("can't start new thread")


def test_vaga_volta_quando_a_thread_da_conexao_nao_inicia(
    tmp_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])
    service = ExecutorService(config, DockerCli(binary=str(tmp_path / "sem-docker")), os.getgid())
    server = ControlServer(config.socket, service, TEST_READ_TIMEOUT_S, max_connections=1)
    try:
        lost, lost_peer = socketlib.socketpair(socketlib.AF_UNIX)
        with lost, lost_peer, monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", _fail_to_start)
            with pytest.raises(RuntimeError):
                server.process_request(lost, None)

        accepted, client = socketlib.socketpair(socketlib.AF_UNIX)
        with client:
            client.settimeout(ANSWER_GUARD_S)
            server.process_request(accepted, None)
            client.sendall(b"GET /v1/attempts/x HTTP/1.1\r\nContent-Length: 0\r\n\r\n")
            status, body = parse_answer(read_until_eof(client))
    finally:
        server.server_close()

    assert (status, body["error"]) == (400, "invalid_attempt_id")


def test_requisicao_cortada_no_prazo_total_nao_chega_ao_docker(
    tmp_path: Path, socket_dir: Path
) -> None:
    docker, calls = recording_docker(tmp_path)
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])

    # A linha chega inteira; o cabeçalho fica pela metade até o prazo total cortar.
    request = f"DELETE /v1/attempts/{ATTEMPT} HTTP/1.1\r\nX-Lento: a".encode()
    with (
        running_server(config, docker, read_timeout_s=5, read_deadline_s=0.3) as socket,
        open_partial_request(socket, request) as stalled,
    ):
        stalled.settimeout(ANSWER_GUARD_S)
        answer = read_until_eof(stalled)
    # Ao sair, o servidor já esperou a thread da conexão terminar.

    assert answer == b""
    assert not calls.exists()


# Os GIDs de produção: gs-exec (área e `--user` do executor) e gs-ctl (só o socket; D11).
EXECUTOR_GROUP_GID = 61001
SOCKET_GROUP_GID = 61002


class _StoppedServer:
    def serve_forever(self) -> None:
        """Volta na hora: o teste só quer ver com que GIDs o main montou o serviço."""

    def shutdown(self) -> None:
        """Nada a encerrar."""

    def server_close(self) -> None:
        """Nada a fechar."""


def _run_main_with_host_groups(
    tmp_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch, groups: dict[str, int]
) -> tuple[int, dict[str, int]]:
    main_module = importlib.import_module("executor_ctl.__main__")
    config = replace(make_config(tmp_path, socket_dir), group="gs-exec", socket_group="gs-ctl")
    seen: dict[str, int] = {}

    def fake_service(_config: object, _docker: object, executor_gid: int) -> object:
        seen["executor"] = executor_gid
        return object()

    def fake_bind(_config: object, _service: object, socket_group_id: int) -> _StoppedServer:
        seen["socket"] = socket_group_id
        return _StoppedServer()

    def getgrnam(name: str) -> SimpleNamespace:
        if name not in groups:
            raise KeyError(name)
        return SimpleNamespace(gr_gid=groups[name])

    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module, "load_config", lambda _path: config)
    monkeypatch.setattr(main_module.grp, "getgrnam", getgrnam)
    monkeypatch.setattr(main_module, "ExecutorService", fake_service)
    monkeypatch.setattr(main_module, "bind_server", fake_bind)
    monkeypatch.setattr(main_module.signal, "signal", lambda *_args: None)
    return main_module.main(["/etc/goblinshift/executor-ctl.json"]), seen


def test_gid_do_executor_difere_do_grupo_do_socket(
    tmp_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, seen = _run_main_with_host_groups(
        tmp_path,
        socket_dir,
        monkeypatch,
        {"gs-exec": EXECUTOR_GROUP_GID, "gs-ctl": SOCKET_GROUP_GID},
    )
    options = profile.run_options(
        uuid.UUID(ATTEMPT), work_dir=tmp_path, runtime="runsc", executor_gid=seen["executor"]
    )

    assert code == 0
    assert f"--user={profile.EXECUTOR_UID}:{EXECUTOR_GROUP_GID}" in options
    assert seen["socket"] == SOCKET_GROUP_GID


def test_main_sem_o_grupo_do_socket_no_host_nao_sobe(
    tmp_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, seen = _run_main_with_host_groups(
        tmp_path, socket_dir, monkeypatch, {"gs-exec": EXECUTOR_GROUP_GID}
    )

    assert code == EXIT_HOST_NOT_READY
    assert "socket" not in seen


def test_main_com_gs_ctl_no_mesmo_gid_do_gs_exec_nao_sobe(
    tmp_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Dois nomes, um GID (`groupadd -o`): o executor voltaria a alcançar o socket.
    code, seen = _run_main_with_host_groups(
        tmp_path,
        socket_dir,
        monkeypatch,
        {"gs-exec": EXECUTOR_GROUP_GID, "gs-ctl": EXECUTOR_GROUP_GID},
    )

    assert code == EXIT_HOST_NOT_READY
    assert "socket" not in seen


def _connect_as(socket_path: Path, uid: int, gid: int, extra_groups: list[int]) -> str:
    """Conecta num processo filho com a identidade dada; devolve o resultado do `connect`."""
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - roda no filho
        outcome = b"erro"
        try:
            os.setgroups(extra_groups)
            os.setgid(gid)
            os.setuid(uid)
            with socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM) as client:
                client.connect(str(socket_path))
            outcome = b"conectou"
        except PermissionError:
            outcome = b"PermissionError"
        finally:
            # Qualquer outra exceção também termina aqui: o filho relata e nunca volta ao pytest.
            os.write(write_end, outcome)
            os._exit(0)
    os.close(write_end)
    try:
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        return os.read(read_end, 64).decode()
    finally:
        os.close(read_end)


@pytest.mark.needs_root
def test_sandbox_nao_conecta_no_socket(tmp_path: Path, socket_dir: Path) -> None:
    # Diretório atravessável por todos: a única barreira que sobra é o modo 0660 do socket.
    socket_dir.chmod(0o755)
    config = make_config(tmp_path, socket_dir)
    service = ExecutorService(config, DockerCli(binary=str(tmp_path / "sem-docker")), 0)
    server = bind_server(config, service, SOCKET_GROUP_GID)
    try:
        sandbox = _connect_as(config.socket, profile.EXECUTOR_UID, EXECUTOR_GROUP_GID, [])
        # Controle: a mesma identidade, com o grupo do socket, conecta (o teste não mente).
        member = _connect_as(
            config.socket, profile.EXECUTOR_UID, EXECUTOR_GROUP_GID, [SOCKET_GROUP_GID]
        )
    finally:
        server.server_close()

    assert sandbox == "PermissionError"
    assert member == "conectou"


def test_fechar_o_servidor_depois_de_uma_thread_que_nao_iniciou_nao_falha(
    tmp_path: Path, socket_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, socket_dir, allowed=[ALLOWED])
    service = ExecutorService(config, DockerCli(binary=str(tmp_path / "sem-docker")), os.getgid())
    server = ControlServer(config.socket, service, TEST_READ_TIMEOUT_S)
    lost, lost_peer = socketlib.socketpair(socketlib.AF_UNIX)
    with lost, lost_peer, monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", _fail_to_start)
        with pytest.raises(RuntimeError):
            server.process_request(lost, None)

    # Sem outra conexão no meio: o join do fechamento não pode alcançar a thread que não nasceu.
    server.server_close()

    assert not config.socket.exists()
