"""`context.json` opcional ao lado do `job.json`: digest da imagem para o manifesto (ADR 020)."""

from pathlib import Path

import pytest

from goblinshift.processing.executor.job import (
    CONTEXT_MAX_BYTES,
    ExecutionContext,
    JobError,
    read_context,
)

IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "e" * 64


def write(tmp_path: Path, data: str | bytes) -> Path:
    path = tmp_path / "context.json"
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def test_le_a_imagem_do_executor(tmp_path: Path) -> None:
    path = write(tmp_path, f'{{"executor_image": "{IMAGE}"}}')

    assert read_context(path) == ExecutionContext(executor_image=IMAGE)


def test_arquivo_ausente_e_a_janela_de_deploy_sem_imagem(tmp_path: Path) -> None:
    assert read_context(tmp_path / "context.json") == ExecutionContext(executor_image=None)


def test_imagem_null_e_aceita(tmp_path: Path) -> None:
    path = write(tmp_path, '{"executor_image": null}')

    assert read_context(path).executor_image is None


def test_imagem_de_desenvolvimento_sem_digest_e_aceita(tmp_path: Path) -> None:
    path = write(tmp_path, '{"executor_image": "local-dev"}')

    assert read_context(path).executor_image == "local-dev"


def test_chave_desconhecida_de_um_worker_mais_novo_e_ignorada(tmp_path: Path) -> None:
    path = write(tmp_path, f'{{"executor_image": "{IMAGE}", "outra": 1}}')

    assert read_context(path).executor_image == IMAGE


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("[]", id="lista"),
        pytest.param("{", id="json-quebrado"),
        pytest.param('{"executor_image": 1}', id="numero"),
        pytest.param('{"executor_image": ""}', id="vazia"),
        pytest.param('{"executor_image": "a\\u0000b"}', id="controle"),
        pytest.param('{"executor_image": "a", "executor_image": "b"}', id="chave-repetida"),
        pytest.param('{"executor_image": NaN}', id="nan"),
        pytest.param("{}", id="sem-a-chave"),
    ],
)
def test_contexto_fora_do_contrato_e_job_error(tmp_path: Path, raw: str) -> None:
    with pytest.raises(JobError):
        read_context(write(tmp_path, raw))


def test_contexto_acima_do_teto_e_job_error(tmp_path: Path) -> None:
    path = write(tmp_path, b" " * (CONTEXT_MAX_BYTES + 1))

    with pytest.raises(JobError, match="bytes"):
        read_context(path)


def test_contexto_que_e_symlink_nao_e_seguido(tmp_path: Path) -> None:
    target = tmp_path / "alvo.json"
    target.write_text(f'{{"executor_image": "{IMAGE}"}}')
    (tmp_path / "context.json").symlink_to(target)

    with pytest.raises(JobError):
        read_context(tmp_path / "context.json")
