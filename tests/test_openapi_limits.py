"""Limites do domínio publicados no contrato OpenAPI (achados do Schemathesis, job "Contrato").

O Schemathesis gera entradas que a spec permite e confere que a API as aceita. Cada limite que o
domínio impõe e o JSON Schema consegue expressar tem de estar na spec; senão o cliente gerado
pelos tipos não sabe dele e o teste de contrato acusa a API de recusar entrada válida.
"""

import json
import re
import sys
import unicodedata
from typing import Any

from goblinshift.entrypoints.openapi_export import render_openapi
from goblinshift.files.uploads import UPLOAD_NAME_PATTERN

SPEC: dict[str, Any] = json.loads(render_openapi())
# Caracteres que o domínio recusa no nome de um upload (files.uploads, 3.6). Os surrogates soltos
# (Cs) também são recusados, mas ficam fora do padrão publicado (ver UPLOAD_NAME_PATTERN).
FORBIDDEN_CATEGORIES = {"Cc", "Zl", "Zp"}
SURROGATES = range(0xD800, 0xE000)
SEPARATORS = {"/", "\\"}


def _json_body(path: str, method: str) -> Any:
    operation = SPEC["paths"][path][method]
    return operation["requestBody"]["content"]["application/json"]["schema"]


def _parameter(path: str, method: str, name: str) -> Any:
    [parameter] = [p for p in SPEC["paths"][path][method]["parameters"] if p["name"] == name]
    return parameter


def test_lote_de_uploads_publica_de_1_a_100_itens() -> None:
    items = _json_body("/api/v1/uploads", "post")["properties"]["items"]

    assert (items["minItems"], items["maxItems"]) == (1, 100)


def test_nome_do_upload_publica_tamanho_e_caracteres_proibidos() -> None:
    name = _json_body("/api/v1/uploads", "post")["properties"]["items"]["items"]["properties"]
    name = name["name"]

    assert (name["minLength"], name["maxLength"]) == (1, 255)
    assert name["pattern"] == UPLOAD_NAME_PATTERN


def test_tamanho_do_upload_publica_de_0_a_32_mib() -> None:
    size = _json_body("/api/v1/uploads", "post")["properties"]["items"]["items"]["properties"]
    size = size["size"]

    assert (size["minimum"], size["maximum"]) == (0, 32 * 1024 * 1024)


def test_padrao_do_nome_recusa_exatamente_separadores_e_controles() -> None:
    pattern = re.compile(UPLOAD_NAME_PATTERN)
    disagreements = [
        f"U+{codepoint:04X}"
        for codepoint in range(sys.maxunicode + 1)
        if codepoint not in SURROGATES
        and (pattern.fullmatch(f"a{chr(codepoint)}b") is None) is not _is_forbidden(chr(codepoint))
    ]

    assert disagreements == []


def _is_forbidden(char: str) -> bool:
    return char in SEPARATORS or unicodedata.category(char) in FORBIDDEN_CATEGORIES


def test_idempotency_key_publica_o_formato_aceito_sem_null() -> None:
    parameter = _parameter("/api/v1/runs", "post", "Idempotency-Key")

    # Header ausente é "sem chave"; `null` faria o cliente gerado enviar o texto "null".
    assert parameter["required"] is False
    assert parameter["schema"] == {"type": "string", "pattern": "^[A-Za-z0-9_-]{16,64}$"}


def test_rotas_autenticadas_publicam_429_com_retry_after() -> None:
    for path, operations in SPEC["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for operation in operations.values():
            response = operation["responses"]["429"]
            assert "application/problem+json" in response["content"]
            retry = response["headers"]["Retry-After"]
            assert retry["required"] is True
            assert retry["schema"] == {"type": "string", "pattern": "^[0-9]+$"}


def test_comandos_json_publicam_erros_do_decodificador_compartilhado() -> None:
    for operations in SPEC["paths"].values():
        for operation in operations.values():
            content = operation.get("requestBody", {}).get("content", {})
            if "application/json" in content:
                assert {"400", "413", "415"} <= operation["responses"].keys()


def test_contagem_por_coluna_do_detalhe_publica_os_tetos() -> None:
    schemas = SPEC["components"]["schemas"]
    columns = schemas["FormulaCellsResponse"]["properties"]["columns"]
    name = schemas["FormulaColumnResponse"]["properties"]["name"]
    cells = schemas["FormulaColumnResponse"]["properties"]["cells"]

    assert (columns["minItems"], columns["maxItems"]) == (1, 10)
    assert (name["minLength"], name["maxLength"]) == (1, 120)
    assert cells["minimum"] == 1
    assert schemas["FormulaCellsResponse"]["properties"]["more_columns"]["minimum"] == 0
