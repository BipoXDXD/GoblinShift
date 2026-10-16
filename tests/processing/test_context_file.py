"""`context.json` opcional ao lado do `job.json`: digest da imagem (ADR 021) e uploads (ADR 035)."""

from pathlib import Path

import pytest

from goblinshift.processing.executor.job import (
    CONTEXT_MAX_BYTES,
    ExecutionContext,
    JobError,
    read_context,
    with_upload_ids,
)
from goblinshift.processing.recipe import InputFile

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


UPLOAD_A = "4eb3e442-3f30-4d83-8d34-cb18a99a46cb"
UPLOAD_B = "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"


def test_le_os_uploads_das_entradas_na_ordem(tmp_path: Path) -> None:
    # ADR 035: o select do PDF Press cita uploads; o executor os liga às entradas pela ordem.
    path = write(
        tmp_path,
        f'{{"executor_image": "{IMAGE}", "input_upload_ids": ["{UPLOAD_B}", "{UPLOAD_A}"]}}',
    )

    assert read_context(path) == ExecutionContext(
        executor_image=IMAGE, input_upload_ids=(UPLOAD_B, UPLOAD_A)
    )


def test_contexto_sem_uploads_e_o_worker_anterior(tmp_path: Path) -> None:
    path = write(tmp_path, f'{{"executor_image": "{IMAGE}"}}')

    assert read_context(path).input_upload_ids is None


@pytest.mark.parametrize(
    "uploads",
    [
        pytest.param('"x"', id="texto"),
        pytest.param("[1]", id="numero"),
        pytest.param('["nao-e-uuid"]', id="nao-uuid"),
        pytest.param(f'["{UPLOAD_A.upper()}"]', id="maiusculas"),
        pytest.param(f'["{UPLOAD_A}", "{UPLOAD_A}"]', id="repetido"),
    ],
)
def test_uploads_fora_do_contrato_sao_job_error(tmp_path: Path, uploads: str) -> None:
    path = write(tmp_path, f'{{"executor_image": "{IMAGE}", "input_upload_ids": {uploads}}}')

    with pytest.raises(JobError, match="input_upload_ids"):
        read_context(path)


def test_uploads_viram_o_upload_id_de_cada_entrada(tmp_path: Path) -> None:
    entries = [
        InputFile(0, tmp_path / "0000.bin", "a.pdf", 1, "0" * 64),
        InputFile(1, tmp_path / "0001.bin", "b.pdf", 1, "1" * 64),
    ]

    linked = with_upload_ids(entries, (UPLOAD_A, UPLOAD_B))

    assert [entry.upload_id for entry in linked] == [UPLOAD_A, UPLOAD_B]


def test_sem_uploads_no_contexto_as_entradas_ficam_sem_upload_id(tmp_path: Path) -> None:
    entries = [InputFile(0, tmp_path / "0000.bin", "a.pdf", 1, "0" * 64)]

    assert with_upload_ids(entries, None) == entries


def test_quantidade_de_uploads_diferente_das_entradas_e_job_error(tmp_path: Path) -> None:
    entries = [InputFile(0, tmp_path / "0000.bin", "a.pdf", 1, "0" * 64)]

    with pytest.raises(JobError, match="input_upload_ids"):
        with_upload_ids(entries, (UPLOAD_A, UPLOAD_B))
