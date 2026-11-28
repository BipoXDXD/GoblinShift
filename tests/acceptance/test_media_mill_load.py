"""Medição do pior caso do Media Mill na VPS (deploy/acceptance/media_mill_load.py).

O script roda no host, como root e sob runsc; aqui fica a parte pura: o job que ele monta a partir
do diretório de vídeos, com os parâmetros já resolvidos pelo catálogo.
"""

import hashlib
from pathlib import Path
from typing import Any

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


def test_parametros_da_v2_sao_opus_192_sem_corte_na_forma_resolvida() -> None:
    assert CATALOG.resolve("media-mill", 2, media.PARAMETERS_V2) == media.PARAMETERS_V2
    assert (media.PARAMETERS_V2["audio_format"], media.PARAMETERS_V2["audio_bitrate_kbps"]) == (
        "opus",
        192,
    )
    assert media.PARAMETERS_V2["trim_end_ms"] is None


def test_job_da_v2_pede_a_versao_2(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"a")

    expected = media.expected_for(tmp_path, 2)

    assert (expected["recipe_version"], expected["parameters"]) == (2, media.PARAMETERS_V2)


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


@pytest.fixture
def host_stubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """O main sem root, sem host e sem medir; a lista recebe de onde a imagem veio."""
    steps: list[str] = []

    def fake_measurement(_measurement: Any) -> int:
        return 0

    (tmp_path / "a.mp4").write_bytes(b"a")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr(
        media, "load_config", lambda _path: type("Config", (), {"group": "g", "socket_group": "c"})
    )
    monkeypatch.setattr("grp.getgrnam", lambda _name: type("Group", (), {"gr_gid": 1}))
    monkeypatch.setattr(load, "build_image", lambda: steps.append("build"))
    monkeypatch.setattr(load, "require_image", lambda: steps.append("require"))
    monkeypatch.setattr(load, "measure_with_maintenance_suspended", fake_measurement)
    return steps


def test_por_padrao_constroi_a_imagem_do_checkout(host_stubs: list[str], tmp_path: Path) -> None:
    assert media.main([str(tmp_path)]) == 0

    assert host_stubs == ["build"]


def test_version_2_mede_com_o_criterio_da_v2(
    host_stubs: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured: list[tuple[int, str]] = []

    def fake_measure(_host: Any, _videos: Path, expected: dict[str, Any], criterion: str) -> int:
        measured.append((expected["recipe_version"], criterion))
        return 0

    monkeypatch.setattr(load, "measure_with_maintenance_suspended", lambda run: run())
    monkeypatch.setattr(load, "measure", fake_measure)
    monkeypatch.setattr("acceptance.run.Host.from_config", lambda _config: None)

    assert media.main(["--version", "2", str(tmp_path)]) == 0

    assert measured == [(2, media.CRITERION_V2)]


def test_skip_build_usa_a_imagem_retagueada_sem_construir(
    host_stubs: list[str], tmp_path: Path
) -> None:
    assert media.main(["--skip-build", str(tmp_path)]) == 0

    assert host_stubs == ["require"]


def test_sem_o_diretorio_dos_videos_sai_com_2_sem_tocar_na_imagem(
    host_stubs: list[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        media.main([])

    assert stopped.value.code == 2
    assert host_stubs == []
