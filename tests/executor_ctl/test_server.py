"""Protocolo HTTP do executor-ctl sem Docker: tudo aqui é recusado antes de chamar o CLI."""

import http.client
import json
import socket as socketlib
import stat
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from executor_ctl.client import UnixHTTPConnection, call
from executor_ctl.docker_cli import DockerCli
from executor_ctl.server import MAX_BODY_BYTES

from .conftest import make_config, running_server

ATTEMPT = "0193a3f0-7b2c-7def-8a01-23456789abcd"
ALLOWED = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
OTHER_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "f" * 64


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
        ALLOWED.replace("@", "%40"),
        ALLOWED + "%20--privileged",
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
