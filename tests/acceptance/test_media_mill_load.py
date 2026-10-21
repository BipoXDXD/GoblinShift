"""Medição do pior caso do Media Mill na VPS (deploy/acceptance/media_mill_load.py).

O script roda no host, como root e sob runsc; aqui fica a parte pura: o job que ele monta a partir
do diretório de vídeos, com os parâmetros já resolvidos pelo catálogo.
"""

import hashlib
from pathlib import Path

import pytest
from acceptance import image_forge_load as load
from acceptance import media_mill_load as media

from goblinshift.catalog import CATALOG


def test_job_lista_os_videos_em_ordem_com_tamanho_e_hash(tmp_path: Path) -> None:
    (tmp_path / "b.mp4").write_bytes(b"bb")
    (tmp_path / "a.webm").write_bytes(b"a")
    (tmp_path / "leia-me.txt").write_bytes(b"x")

    job, copies = load.plan_job(media.expected_for(tmp_path))

    assert [(e["position"], e["original_name"], e["size"]) for e in job["inputs"]] == [
        (0, "a.webm", 1),
        (1, "b.mp4", 2),
    ]
    assert job["inputs"][0]["sha256"] == hashlib.sha256(b"a").hexdigest()
    assert copies == [("a.webm", "0000.bin"), ("b.mp4", "0001.bin")]
    assert job["recipe_id"] == "media-mill"


def test_parametros_sao_a_forma_resolvida_pelo_catalogo() -> None:
    assert CATALOG.resolve("media-mill", 1, media.PARAMETERS) == media.PARAMETERS


@pytest.mark.parametrize("count", [0, 6])
def test_diretorio_sem_videos_ou_com_mais_de_cinco_e_recusado(tmp_path: Path, count: int) -> None:
    for index in range(count):
        (tmp_path / f"{index}.mp4").write_bytes(b"x")

    with pytest.raises(ValueError, match="de 1 a 5"):
        media.expected_for(tmp_path)


def test_veredito_usa_o_criterio_do_media_mill() -> None:
    outcome = load.judge(
        exit_code=0,
        duration_s=100,
        timed_out=False,
        summary={"inputs": 5, "ok": 5},
        criterion=media.CRITERION,
    )

    assert (outcome.criterion, outcome.passed) == (media.CRITERION, True)
