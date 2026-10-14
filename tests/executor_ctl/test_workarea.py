"""Regras da área de trabalho: uma tentativa por vez, nada fora de <area>/<id> (ADR 005)."""

import os
import shutil
import stat
import uuid
from pathlib import Path

import pytest

from executor_ctl import workarea
from executor_ctl.workarea import WorkAreaDirtyError, WorkDirMissingError

ATTEMPT = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcd")
OTHER = uuid.UUID("0193a3f0-7b2c-7def-8a01-000000000000")


@pytest.fixture
def area(tmp_path: Path) -> Path:
    path = tmp_path / "exec-work"
    path.mkdir()
    return path


def make_attempt_dir(area: Path, attempt_id: uuid.UUID = ATTEMPT) -> Path:
    """O que o worker prepara antes do start: job.json e inputs/."""
    work_dir = area / str(attempt_id)
    (work_dir / "inputs").mkdir(parents=True)
    (work_dir / "job.json").write_text("{}")
    return work_dir


def test_area_so_com_o_diretorio_da_tentativa_e_aceita(area: Path) -> None:
    work_dir = make_attempt_dir(area)

    assert workarea.prepare_for_start(area, ATTEMPT, os.getgid()).path == work_dir


def test_lost_found_do_ext4_nao_suja_a_area(area: Path) -> None:
    (area / "lost+found").mkdir()
    make_attempt_dir(area)

    workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_diretorio_de_outra_tentativa_suja_a_area(area: Path) -> None:
    make_attempt_dir(area)
    make_attempt_dir(area, OTHER)

    with pytest.raises(WorkAreaDirtyError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_arquivo_solto_na_raiz_suja_a_area(area: Path) -> None:
    make_attempt_dir(area)
    (area / "resto.bin").write_bytes(b"x")

    with pytest.raises(WorkAreaDirtyError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_diretorio_da_tentativa_ausente_e_recusado(area: Path) -> None:
    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_area_inexistente_e_recusada(tmp_path: Path) -> None:
    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(tmp_path / "nao-montada", ATTEMPT, os.getgid())


def test_diretorio_da_tentativa_como_symlink_e_recusado(area: Path, tmp_path: Path) -> None:
    # Um symlink faria o Docker montar em /work um diretório qualquer do host.
    target = tmp_path / "fora"
    (target / "inputs").mkdir(parents=True)
    (area / str(ATTEMPT)).symlink_to(target)

    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_inputs_como_symlink_e_recusado(area: Path, tmp_path: Path) -> None:
    work_dir = area / str(ATTEMPT)
    work_dir.mkdir()
    (work_dir / "inputs").symlink_to(tmp_path)

    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_inputs_ausente_e_recusado(area: Path) -> None:
    (area / str(ATTEMPT)).mkdir()

    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_out_como_symlink_e_recusado_sem_tocar_no_alvo(area: Path, tmp_path: Path) -> None:
    # Root seguindo o link faria chmod num arquivo qualquer do host.
    work_dir = make_attempt_dir(area)
    target = tmp_path / "alvo"
    target.mkdir(mode=0o700)
    target.chmod(0o700)
    (work_dir / "out").symlink_to(target)

    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())

    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_out_como_arquivo_e_recusado(area: Path) -> None:
    work_dir = make_attempt_dir(area)
    (work_dir / "out").write_text("não é diretório")

    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


def test_symlink_da_tentativa_nao_muda_o_modo_do_alvo(area: Path, tmp_path: Path) -> None:
    target = tmp_path / "fora"
    (target / "inputs").mkdir(parents=True)
    target.chmod(0o700)
    (area / str(ATTEMPT)).symlink_to(target)

    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())

    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert not (target / "out").exists()


def test_troca_por_symlink_depois_da_conferencia_e_detectada(area: Path, tmp_path: Path) -> None:
    # A janela entre conferir e o docker run: o caminho passa a apontar para fora da área.
    work_dir = make_attempt_dir(area)
    prepared = workarea.prepare_for_start(area, ATTEMPT, os.getgid())
    outside = tmp_path / "fora"
    (outside / "inputs").mkdir(parents=True)
    work_dir.rename(tmp_path / "original")
    work_dir.symlink_to(outside)

    with pytest.raises(WorkDirMissingError):
        prepared.confirm_unchanged()


def test_inputs_trocado_depois_da_conferencia_e_detectado(area: Path) -> None:
    work_dir = make_attempt_dir(area)
    prepared = workarea.prepare_for_start(area, ATTEMPT, os.getgid())
    (work_dir / "inputs").rename(work_dir / "inputs-antigo")
    (work_dir / "inputs").mkdir()

    with pytest.raises(WorkDirMissingError):
        prepared.confirm_unchanged()


def test_conferencia_sem_troca_passa(area: Path) -> None:
    make_attempt_dir(area)

    workarea.prepare_for_start(area, ATTEMPT, os.getgid()).confirm_unchanged()


def test_out_ausente_e_criado_gravavel_pelo_grupo(area: Path) -> None:
    work_dir = make_attempt_dir(area)

    workarea.prepare_for_start(area, ATTEMPT, os.getgid())

    mode = (work_dir / "out").stat().st_mode
    assert stat.S_ISDIR(mode)
    assert stat.S_IMODE(mode) & 0o2070 == 0o2070


def test_diretorio_da_tentativa_fica_gravavel_pelo_grupo(area: Path) -> None:
    work_dir = make_attempt_dir(area)
    work_dir.chmod(0o700)

    workarea.prepare_for_start(area, ATTEMPT, os.getgid())

    assert stat.S_IMODE(work_dir.stat().st_mode) == 0o2770
    assert work_dir.stat().st_gid == os.getgid()


def test_remove_apaga_o_diretorio_inteiro(area: Path) -> None:
    work_dir = make_attempt_dir(area)
    (work_dir / "out").mkdir()
    (work_dir / "out" / "resultado.csv").write_text("a,b\n")

    workarea.remove_attempt_dir(area, ATTEMPT)

    assert not work_dir.exists()


def test_remove_nao_toca_outras_tentativas(area: Path) -> None:
    make_attempt_dir(area)
    other = make_attempt_dir(area, OTHER)

    workarea.remove_attempt_dir(area, ATTEMPT)

    assert other.is_dir()


def test_remove_de_diretorio_ausente_e_sucesso(area: Path) -> None:
    workarea.remove_attempt_dir(area, ATTEMPT)


def test_remove_nao_segue_symlink_dentro_da_tentativa(area: Path, tmp_path: Path) -> None:
    work_dir = make_attempt_dir(area)
    target = tmp_path / "fora"
    target.mkdir()
    (target / "importante.txt").write_text("fica")
    (work_dir / "out").symlink_to(target)

    workarea.remove_attempt_dir(area, ATTEMPT)

    assert not work_dir.exists()
    assert (target / "importante.txt").read_text() == "fica"


def test_remocao_recursiva_e_a_versao_segura_contra_symlink() -> None:
    assert shutil.rmtree.avoids_symlink_attacks


def test_remove_de_symlink_apaga_so_o_link(area: Path, tmp_path: Path) -> None:
    target = tmp_path / "fora"
    target.mkdir()
    (target / "importante.txt").write_text("fica")
    (area / str(ATTEMPT)).symlink_to(target)

    workarea.remove_attempt_dir(area, ATTEMPT)

    assert not (area / str(ATTEMPT)).is_symlink()
    assert (target / "importante.txt").read_text() == "fica"
