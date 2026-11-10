"""Preenchimento dos parâmetros de caminho usado pelos testes que percorrem as rotas registradas.

Fail-closed: um parâmetro que a regex não reconhece não pode passar adiante como texto literal
(`/runs/{runId}` chegaria à rota como 404 e o teste de segurança passaria sem testar nada).
"""

import pytest

from tests.security.path_params import fill_path_params


def _fixed(_name: str) -> str:
    return "X"


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("/api/v1/me", "/api/v1/me"),
        ("/api/v1/runs/{run_id}", "/api/v1/runs/X"),
        ("/api/v1/runs/{run_id:uuid}/cancel", "/api/v1/runs/X/cancel"),
        ("/api/v1/runs/{runId}", "/api/v1/runs/X"),
        ("/api/v1/x/{id2}", "/api/v1/x/X"),
        ("/files/{rest:path}", "/files/X"),
        ("/a/{first}/b/{Second_2}", "/a/X/b/X"),
    ],
)
def test_parametro_reconhecido_e_substituido(template: str, expected: str) -> None:
    assert fill_path_params(template, _fixed) == expected


def test_valor_e_escolhido_pelo_nome_sem_o_conversor() -> None:
    seen: list[str] = []

    def by_name(name: str) -> str:
        seen.append(name)
        return name.upper()

    assert fill_path_params("/auth/{provider:str}/{run_id}", by_name) == "/auth/PROVIDER/RUN_ID"
    assert seen == ["provider", "run_id"]


@pytest.mark.parametrize(
    "template",
    ["/x/{}", "/x/{1run}", "/x/{run-id}", "/x/{run id}", "/x/{run_id", "/x/run_id}", "/x/{a:}"],
)
def test_parametro_nao_reconhecido_falha_em_vez_de_passar_literal(template: str) -> None:
    with pytest.raises(ValueError, match="parâmetro de caminho"):
        fill_path_params(template, _fixed)
