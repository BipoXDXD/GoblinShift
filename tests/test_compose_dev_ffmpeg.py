"""FFmpeg do worker de desenvolvimento: o mesmo build mínimo da imagem do executor (ADR 036).

Com o Runner local, a receita roda num subprocesso do container do worker, que é a imagem da
aplicação. O worker do `compose.override.yaml` acrescenta a ela o `ffmpeg` e o `ffprobe` copiados
do estágio `ffmpeg` do `docker/executor.Dockerfile` (mesma versão, mesmo `configure`), em vez do
FFmpeg do sistema, que tem todos os demuxers e protocolos e mudaria o comportamento da receita.
A imagem de produção da aplicação continua sem FFmpeg.
"""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
EXECUTOR_DOCKERFILE = ROOT / "docker" / "executor.Dockerfile"
WORKER_DEV_DOCKERFILE = ROOT / "docker" / "worker-dev.Dockerfile"
FFMPEG_STAGE = "ffmpeg"


def _services(compose_file: str) -> dict[str, dict[str, Any]]:
    document = yaml.safe_load((ROOT / compose_file).read_text(encoding="utf-8"))
    found: dict[str, dict[str, Any]] = document.get("services") or {}
    return found


def _copies_from(dockerfile: Path, source: str) -> list[str]:
    """As instruções `COPY --from=<source>` do Dockerfile, normalizadas em uma linha cada."""
    pattern = re.compile(rf"^COPY --from={re.escape(source)}\s+(.+)$", re.MULTILINE)
    return [" ".join(match.split()) for match in pattern.findall(dockerfile.read_text("utf-8"))]


def _dev_worker_build() -> dict[str, Any]:
    build: dict[str, Any] = _services("compose.override.yaml")["worker"]["build"]
    return build


def _ffmpeg_service_name() -> str:
    reference: str = _dev_worker_build()["additional_contexts"][FFMPEG_STAGE]
    assert reference.startswith("service:"), reference
    return reference.removeprefix("service:")


def test_o_worker_de_desenvolvimento_usa_a_imagem_com_o_ffmpeg_minimo() -> None:
    build = _dev_worker_build()

    assert build["dockerfile"] == str(WORKER_DEV_DOCKERFILE.relative_to(ROOT))
    assert build["additional_contexts"]["app"] == "service:api"


def test_o_ffmpeg_vem_do_estagio_da_imagem_do_executor() -> None:
    service = _services("compose.override.yaml")[_ffmpeg_service_name()]

    assert service["build"]["dockerfile"] == str(EXECUTOR_DOCKERFILE.relative_to(ROOT))
    assert service["build"]["target"] == FFMPEG_STAGE


def test_o_servico_do_ffmpeg_so_existe_para_o_build() -> None:
    # Com zero réplicas, o `docker compose up` constrói o estágio mas não o executa.
    service = _services("compose.override.yaml")[_ffmpeg_service_name()]

    assert service["scale"] == 0


def test_o_worker_copia_os_mesmos_arquivos_que_a_imagem_do_executor() -> None:
    executor = _copies_from(EXECUTOR_DOCKERFILE, FFMPEG_STAGE)

    assert executor, "sem COPY --from=ffmpeg no executor este teste não protege nada"
    assert _copies_from(WORKER_DEV_DOCKERFILE, FFMPEG_STAGE) == executor


def test_o_worker_de_desenvolvimento_parte_da_imagem_da_aplicacao() -> None:
    stages = re.findall(r"^FROM\s+(\S+)", WORKER_DEV_DOCKERFILE.read_text("utf-8"), re.MULTILINE)

    assert stages == ["app"]


def test_a_producao_nao_troca_a_imagem_nem_o_build_do_worker() -> None:
    worker = _services("compose.prod.yaml")["worker"]

    assert "build" not in worker
    assert "image" not in worker


def test_a_imagem_da_aplicacao_continua_sem_ffmpeg() -> None:
    assert "ffmpeg" not in (ROOT / "docker" / "app.Dockerfile").read_text("utf-8").lower()
