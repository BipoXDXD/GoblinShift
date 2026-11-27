"""Regras da área de trabalho: uma tentativa por vez, nada fora de <area>/<id> (ADR 005)."""

import os
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Self

import pytest

from executor_ctl import workarea
from executor_ctl.workarea import (
    UnsafeRemovalError,
    WorkAreaDirtyError,
    WorkDirMissingError,
    WorkDirModeError,
)

pytestmark = pytest.mark.host_python

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


def owner_group_mode(path: Path) -> tuple[int, int, int]:
    info = path.lstat()
    return info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)


def test_start_passa_a_tentativa_ao_ctl_com_os_modos_do_contrato(area: Path) -> None:
    # Em produção o ctl é root: depois disto o worker não troca <id>, inputs/ nem out/.
    work_dir = make_attempt_dir(area)
    (work_dir / "inputs" / "0.csv").write_text("a,b\n")
    (work_dir / "inputs" / "0.csv").chmod(0o660)
    work_dir.chmod(0o700)
    ctl, group = os.geteuid(), os.getgid()

    workarea.prepare_for_start(area, ATTEMPT, group)

    assert owner_group_mode(work_dir) == (ctl, group, 0o3770)
    assert owner_group_mode(work_dir / "inputs") == (ctl, group, 0o2750)
    assert owner_group_mode(work_dir / "inputs" / "0.csv") == (ctl, group, 0o640)
    assert owner_group_mode(work_dir / "out") == (ctl, group, 0o2770)


def test_setgid_limpo_pelo_kernel_reprova_o_start(
    area: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Sem CAP_FSETID e fora do grupo do arquivo, o chmod do root perde o setgid sem erro.
    real_fchmod = os.fchmod

    def fchmod_without_fsetid(fd: int, mode: int) -> None:
        real_fchmod(fd, mode & ~stat.S_ISGID)

    monkeypatch.setattr(os, "fchmod", fchmod_without_fsetid)
    make_attempt_dir(area)

    with pytest.raises(WorkDirModeError, match="3770"):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())


@pytest.mark.parametrize("kind", ["symlink", "diretorio"])
def test_entrada_de_inputs_que_nao_e_arquivo_regular_e_recusada(
    area: Path, tmp_path: Path, kind: str
) -> None:
    work_dir = make_attempt_dir(area)
    target = tmp_path / "alvo.txt"
    target.write_text("de fora")
    target.chmod(0o600)
    entry = work_dir / "inputs" / "0.csv"
    if kind == "symlink":
        entry.symlink_to(target)
    else:
        entry.mkdir()

    with pytest.raises(WorkDirMissingError):
        workarea.prepare_for_start(area, ATTEMPT, os.getgid())

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


WORKER_UID = 10001


# Trocar de UID exige root no Linux: roda no passo com sudo do job Verificações.
@pytest.mark.needs_root
@pytest.mark.parametrize("entry", [".", "inputs", "out"])
def test_worker_nao_renomeia_nada_da_tentativa_depois_do_start(area: Path, entry: str) -> None:
    group = os.getgid()
    area.chmod(0o3770)
    work_dir = make_attempt_dir(area)
    for path in (work_dir, work_dir / "inputs", work_dir / "job.json"):
        os.chown(path, WORKER_UID, group)
    workarea.prepare_for_start(area, ATTEMPT, group)
    source = work_dir / entry if entry != "." else work_dir

    attempt = subprocess.run(
        ["/bin/mv", str(source), str(source.parent / "trocado")],
        user=WORKER_UID,
        group=group,
        extra_groups=[group],
        capture_output=True,
        check=False,
    )

    assert attempt.returncode != 0
    assert source.is_dir() and not source.is_symlink()


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
    assert os.rmdir in os.supports_dir_fd
    assert os.unlink in os.supports_dir_fd
    assert os.scandir in os.supports_fd


def test_plataforma_sem_remocao_por_descritor_recusa_remover(
    area: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work_dir = make_attempt_dir(area)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd - {os.rmdir})

    with pytest.raises(UnsafeRemovalError):
        workarea.remove_attempt_dir(area, ATTEMPT)

    assert work_dir.is_dir()


def build_deep_tree(
    top: Path, *, depth: int, symlink_at: int | None = None, target: Path | None = None
) -> None:
    """`top/d/d/.../d` com `depth` níveis e um arquivo em cada; sem caminho longo (dir_fd)."""
    top.mkdir()
    fd = os.open(top, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for level in range(depth):
            file_fd = os.open("arquivo", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=fd)
            os.close(file_fd)
            if level == symlink_at:
                assert target is not None
                os.symlink(target, "link", dir_fd=fd)
            os.mkdir("d", dir_fd=fd)
            child = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


def run_in_subprocess_with_nofile_limit(
    limit: int, func: Callable[[Path, uuid.UUID], None], area: Path, attempt_id: uuid.UUID
) -> subprocess.CompletedProcess[str]:
    """Chama `func` num processo com RLIMIT_NOFILE baixo, sem mexer no limite do pytest."""
    code = (
        "import importlib, resource, sys, uuid\n"
        "from pathlib import Path\n"
        f"resource.setrlimit(resource.RLIMIT_NOFILE, ({limit}, {limit}))\n"
        f"func = getattr(importlib.import_module({func.__module__!r}), {func.__name__!r})\n"
        "func(Path(sys.argv[1]), uuid.UUID(sys.argv[2]))\n"
    )
    deploy_dir = Path(workarea.__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, "-c", code, str(area), str(attempt_id)],
        env={**os.environ, "PYTHONPATH": str(deploy_dir)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_NOFILE e dir_fd do host")
def test_remove_arvore_mais_funda_que_o_limite_de_descritores(area: Path) -> None:
    build_deep_tree(area / str(ATTEMPT), depth=300)

    result = run_in_subprocess_with_nofile_limit(64, workarea.remove_attempt_dir, area, ATTEMPT)

    assert result.returncode == 0, result.stderr
    assert not (area / str(ATTEMPT)).exists()


def test_remocao_funda_nao_segue_symlink_no_meio(area: Path, tmp_path: Path) -> None:
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "guardado").write_text("x")
    build_deep_tree(area / str(ATTEMPT), depth=50, symlink_at=25, target=outside)

    workarea.remove_attempt_dir(area, ATTEMPT)

    assert (outside / "guardado").read_text() == "x"
    assert not (area / str(ATTEMPT)).exists()


class _ListedEntries:
    """O que `os.scandir(fd)` devolve, com a listagem já lida (o gancho age depois dela)."""

    def __init__(self, entries: list[os.DirEntry[str]]) -> None:
        self._entries = entries

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def __iter__(self) -> Iterator[os.DirEntry[str]]:
        return iter(self._entries)


def hook_scandir_of(
    monkeypatch: pytest.MonkeyPatch, directory: Path, action: Callable[[], object]
) -> None:
    """Roda `action` uma vez, logo depois de listar `directory` e antes do passo seguinte."""
    real_scandir = os.scandir
    watched = directory.stat()
    fired = False

    def scandir_then_act(fd: int) -> _ListedEntries:
        nonlocal fired
        with real_scandir(fd) as listing:
            entries = list(listing)
        opened = os.fstat(fd)
        if not fired and (opened.st_dev, opened.st_ino) == (watched.st_dev, watched.st_ino):
            fired = True
            action()
        return _ListedEntries(entries)

    monkeypatch.setattr(os, "scandir", scandir_then_act)
    # O gancho aceita fd como o original; sem isso a remoção se recusaria a rodar.
    monkeypatch.setattr(os, "supports_fd", os.supports_fd | {scandir_then_act})


def test_subida_com_pai_trocado_recusa(
    area: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "guardado").write_text("x")
    top = area / str(ATTEMPT)
    leaf = top / "a" / "b" / "c"
    leaf.mkdir(parents=True)
    # Enquanto o ctl está em c, alguém move c para fora: o `..` de c deixa de ser b.
    hook_scandir_of(monkeypatch, leaf, lambda: leaf.rename(outside / "c"))

    with pytest.raises(WorkDirMissingError):
        workarea.remove_attempt_dir(area, ATTEMPT)

    assert (outside / "guardado").read_text() == "x"
    assert (outside / "c").is_dir()
    assert top.is_dir()


def test_symlink_trocado_entre_scandir_e_open_nao_e_seguido(
    area: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "guardado").write_text("x")
    top = area / str(ATTEMPT)
    (top / "a").mkdir(parents=True)

    def swap_for_symlink() -> None:
        (top / "a").rename(tmp_path / "movido")
        (top / "a").symlink_to(outside)

    # A listagem de top ainda diz que `a` é diretório; quando o ctl o abre, é um symlink.
    hook_scandir_of(monkeypatch, top, swap_for_symlink)

    with pytest.raises(WorkDirMissingError):
        workarea.remove_attempt_dir(area, ATTEMPT)

    assert (outside / "guardado").read_text() == "x"
    assert top.is_dir()


def test_remove_de_symlink_apaga_so_o_link(area: Path, tmp_path: Path) -> None:
    target = tmp_path / "fora"
    target.mkdir()
    (target / "importante.txt").write_text("fica")
    (area / str(ATTEMPT)).symlink_to(target)

    workarea.remove_attempt_dir(area, ATTEMPT)

    assert not (area / str(ATTEMPT)).is_symlink()
    assert (target / "importante.txt").read_text() == "fica"
