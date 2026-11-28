"""Erros que o próprio Nginx gera saem no problem+json da API (issue #442), sem Docker.

A SPA só reconhece um erro se o corpo validar contra o `ProblemDetails` publicado no OpenAPI, com
`code` do catálogo; senão ele vira "erro inesperado". Estes testes leem o template e conferem cada
`return NNN '…'` contra o schema e o catálogo (status, título e `type` do `code`), os `map` que
preenchem `instance` e `request_id`, e os cabeçalhos das locations de erro contra os da API. O
comportamento no Nginx real fica nos testes com marca `nginx` (test_nginx_edge_limits.py,
test_nginx_auth_limit.py e test_nginx_logs.py).
"""

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from goblinshift.entrypoints.api.problems import (
    PROBLEMS,
    ProblemCode,
    ProblemDetails,
)
from goblinshift.entrypoints.api.security_headers import SecurityHeadersMiddleware

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docker" / "nginx" / "templates" / "default.conf.template"
SNIPPETS = ROOT / "docker" / "nginx" / "snippets"
OPENAPI = ROOT / "web" / "openapi" / "openapi.json"
PROBLEM_HEADERS = "include /etc/nginx/snippets/problem-headers.conf;"
HSTS_DIRECTIVE = "add_header Strict-Transport-Security $hsts always;"

# Exemplos para as variáveis que os corpos podem usar. Qualquer outra variável reprova: o
# `$request_uri` cru traria a query string e aspas para dentro do JSON.
EXAMPLE_INSTANCE = "/api/v1/runs"
EXAMPLE_REQUEST_ID = "0f8e3c2a-9b1d-4e7f-a6c5-3d2b1a0f9e8d"
BODY_VARIABLES = {
    "$problem_instance": EXAMPLE_INSTANCE,
    "$problem_request_id": EXAMPLE_REQUEST_ID,
}
# Os erros que o Nginx responde sozinho: limite de requisições, teto de corpo e API fora do ar.
EXPECTED_CODES = {
    ProblemCode.RATE_LIMITED,
    ProblemCode.BODY_TOO_LARGE,
    ProblemCode.SERVICE_UNAVAILABLE,
}
# Locations que encaminham à API e por isso traduzem o 413 e o 502/504 do Nginx.
API_LOCATIONS = ("location /api/", r"location ~ ^/api/v1/uploads/[^/]+/content$")
BODY_TOO_LARGE_PAGE = "error_page 413 = @body_too_large;"
UPSTREAM_UNAVAILABLE_PAGE = "error_page 502 504 = @upstream_unavailable;"

_RETURN = re.compile(r"^\s*return (?P<status>\d{3}) '(?P<body>[^']*)';\s*$", re.MULTILINE)
_VARIABLE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")


def _template() -> str:
    return TEMPLATE.read_text()


def _directives(text: str) -> list[str]:
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _location_blocks() -> dict[str, list[str]]:
    """Diretivas de cada `location` (o template não aninha blocos dentro delas)."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in _directives(_template()):
        if line.startswith("location "):
            current = line.removesuffix("{").strip()
            blocks[current] = []
        elif current is not None and line == "}":
            current = None
        elif current is not None:
            blocks[current].append(line)
    return blocks


def _returns() -> list[Any]:
    """Cada `return NNN '…'` do template, com o status e o `code` no id do caso."""
    cases = []
    for index, match in enumerate(_RETURN.finditer(_template())):
        code = re.search(r'"code":"(\w+)"', match["body"])
        case_id = f"{index}-{match['status']}-{code[1] if code else 'sem-code'}"
        cases.append(pytest.param(int(match["status"]), match["body"], id=case_id))
    return cases


def _render(body: str) -> dict[str, Any]:
    unknown = set(_VARIABLE.findall(body)) - set(BODY_VARIABLES)
    assert not unknown, f"variável fora da lista em corpo de erro: {sorted(unknown)}"
    for variable, example in BODY_VARIABLES.items():
        body = body.replace(variable, example)
    rendered: dict[str, Any] = json.loads(body)
    return rendered


def _problem_schema() -> dict[str, Any]:
    schemas = json.loads(OPENAPI.read_text())["components"]["schemas"]
    schema: dict[str, Any] = schemas["ProblemDetails"]
    schema["code_enum"] = schemas["ProblemCode"]["enum"]
    return schema


def _map_entries(variable: str) -> list[tuple[str, str]]:
    """Pares (chave, valor) do `map` que define `variable`, na ordem do template."""
    block = re.search(
        rf"^map \S+ \${variable} \{{\n(?P<body>.*?)^\}}", _template(), re.MULTILINE | re.DOTALL
    )
    assert block, f"map de ${variable} não encontrado"
    entries = []
    for line in _directives(block["body"]):
        key, value = line.removesuffix(";").rsplit(None, 1)
        entries.append((key.strip().strip('"'), value.strip('"')))
    return entries


def _apply_map(variable: str, source: str) -> str:
    """Avalia o `map` como o Nginx: a primeira regex que casa; senão, o `default`."""
    default = ""
    for key, value in _map_entries(variable):
        if key == "default":
            default = value
            continue
        assert key.startswith("~"), f"chave literal inesperada no map de ${variable}: {key}"
        # Python escreve o grupo nomeado como `(?P<nome>`; o PCRE do Nginx aceita `(?<nome>`.
        pattern = re.sub(r"\(\?<(?=[A-Za-z_])", "(?P<", key.removeprefix("~"))
        match = re.search(pattern, source)
        if match:
            # `$nome` do Nginx vira `\g<nome>` do Python.
            return match.expand(re.sub(r"\$(\w+)", r"\\g<\1>", value))
    return default


# --- corpos -----------------------------------------------------------------------------------


def test_template_responde_os_erros_do_nginx_com_codigos_do_catalogo() -> None:
    codes = {ProblemCode(_render(case.values[1])["code"]) for case in _returns()}

    assert codes == EXPECTED_CODES


@pytest.mark.parametrize(("status", "body"), _returns())
def test_corpo_valida_contra_o_problem_details_do_openapi(status: int, body: str) -> None:
    schema = _problem_schema()
    problem = _render(body)

    assert set(schema["required"]) <= set(problem) <= set(schema["properties"])
    for field in ("type", "title", "detail", "instance", "request_id"):
        assert isinstance(problem[field], str), field
    assert type(problem["status"]) is int
    assert problem["code"] in schema["code_enum"]
    ProblemDetails.model_validate(problem)


@pytest.mark.parametrize(("status", "body"), _returns())
def test_corpo_segue_o_catalogo_do_codigo(status: int, body: str) -> None:
    problem = _render(body)
    code = ProblemCode(problem["code"])

    assert status == problem["status"] == PROBLEMS[code].status
    assert problem["title"] == PROBLEMS[code].title
    assert problem["type"] == code.type_uri


@pytest.mark.parametrize(("status", "body"), _returns())
def test_corpo_traz_instance_e_request_id_dos_maps(status: int, body: str) -> None:
    problem = _render(body)

    assert problem["instance"] == EXAMPLE_INSTANCE
    assert problem["request_id"] == EXAMPLE_REQUEST_ID


# --- instance e request_id ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_uri", "instance"),
    [
        ("/api/v1/runs", "/api/v1/runs"),
        ("/api/v1/runs?token=CANARY&q=1", "/api/v1/runs"),
        ("/auth/login/google?", "/auth/login/google"),
        ("/api/v1/uploads/a%22b/content", "/api/v1/uploads/a%22b/content"),
        ("/api/v1/~x_y.z-1", "/api/v1/~x_y.z-1"),
        ('/api/v1/a"b', "/"),
        ("/api/v1/a\\b", "/"),
        ("/api/v1/a<b>", "/"),
        ("/api/v1/a b", "/"),
        ("api/sem-barra", "/"),
        ("", "/"),
    ],
)
def test_instance_e_so_o_caminho_seguro_da_requisicao(request_uri: str, instance: str) -> None:
    assert _apply_map("problem_instance", request_uri) == instance


def test_request_id_tem_o_formato_de_uuid_da_api() -> None:
    nginx_request_id = uuid.uuid4().hex  # 32 dígitos hexadecimais, como o $request_id

    formatted = _apply_map("problem_request_id", nginx_request_id)

    assert formatted == str(uuid.UUID(nginx_request_id))


# --- locations ---------------------------------------------------------------------------------


@pytest.mark.parametrize("location", API_LOCATIONS)
def test_location_da_api_traduz_413_e_falha_de_upstream(location: str) -> None:
    directives = _location_blocks()[location]

    assert BODY_TOO_LARGE_PAGE in directives
    assert UPSTREAM_UNAVAILABLE_PAGE in directives


def test_auth_nao_traduz_413_nem_falha_de_upstream() -> None:
    directives = _location_blocks()["location /auth/"]

    assert not any(line.startswith(("error_page 413", "error_page 502")) for line in directives)


# Named locations que redirecionam a navegação, não respondem erro: o POST do compartilhamento sem
# service worker vai para a Oficina (ADR 062, tests/test_nginx_share_target.py).
REDIRECT_LOCATIONS = frozenset({"location @share_unavailable"})


def _named_error_locations() -> list[str]:
    return sorted(
        name
        for name in _location_blocks()
        if name.startswith("location @") and name not in REDIRECT_LOCATIONS
    )


@pytest.mark.parametrize("location", _named_error_locations())
def test_location_de_erro_e_problem_json_com_os_cabecalhos_da_api(location: str) -> None:
    directives = _location_blocks()[location]

    assert "default_type application/problem+json;" in directives
    assert PROBLEM_HEADERS in directives


def _api_security_headers() -> dict[str, str]:
    """Cabeçalhos que o middleware da API põe numa resposta JSON, com HSTS ligado."""
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    scope: Scope = {"type": "http", "headers": []}
    asyncio.run(SecurityHeadersMiddleware(app, hsts=True)(scope, receive, send))
    return {name.decode(): value.decode() for name, value in sent[0]["headers"]}


def _snippet_headers() -> dict[str, str]:
    headers = {}
    for line in _directives((SNIPPETS / "problem-headers.conf").read_text()):
        match = re.fullmatch(r'add_header (\S+) (?:"(.*)"|(\S+)) always;', line)
        assert match, f"diretiva inesperada no snippet: {line}"
        headers[match[1].lower()] = match[2] if match[2] is not None else match[3]
    return headers


def test_snippet_de_erro_tem_os_cabecalhos_de_seguranca_da_api() -> None:
    expected = _api_security_headers()
    # O valor do HSTS vem do map: vazio, e portanto omitido, fora do HTTPS da borda.
    expected["strict-transport-security"] = "$hsts"

    assert _snippet_headers() == expected


def test_snippet_de_erro_emite_o_hsts_uma_vez() -> None:
    directives = _directives((SNIPPETS / "problem-headers.conf").read_text())

    assert directives.count(HSTS_DIRECTIVE) == 1
