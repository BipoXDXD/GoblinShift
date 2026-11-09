"""Storage em filesystem (docs/GoblinShiftPlan.md, 3.2 e 3.6), sem banco."""

import hashlib
import os
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from goblinshift.files.storage import (
    ContentTooLargeError,
    FileStorage,
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectMissingError,
    TemporarySweep,
)

MIB = 1024 * 1024
MAX_FILE_BYTES = 32 * MIB


class ClientDisconnectedError(Exception):
    pass


def repeated_chunks(total: int, chunk_size: int = MIB) -> Iterator[bytes]:
    """Gera `total` bytes em pedaços, sem materializar o arquivo inteiro em memória."""
    chunk = b"\xab" * chunk_size
    remaining = total
    while remaining > 0:
        piece = chunk[: min(chunk_size, remaining)]
        remaining -= len(piece)
        yield piece


def failing_after(first: bytes) -> Iterator[bytes]:
    yield first
    raise ClientDisconnectedError


def files_under(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


@pytest.fixture
def storage(tmp_path: Path) -> FileStorage:
    return FileStorage(tmp_path / "storage")


def test_grava_conteudo_e_devolve_sha256_e_tamanho(storage: FileStorage) -> None:
    sha256, size = storage.create_exclusive("uploads/a", [b"ola, ", b"mundo"], max_bytes=100)

    assert sha256 == hashlib.sha256(b"ola, mundo").digest()
    assert size == 10
    with storage.open("uploads/a") as stream:
        assert stream.read() == b"ola, mundo"


def test_arquivo_vazio_e_valido(storage: FileStorage) -> None:
    sha256, size = storage.create_exclusive("uploads/vazio", [], max_bytes=0)

    assert size == 0
    assert sha256 == hashlib.sha256(b"").digest()
    assert storage.stat("uploads/vazio").size_bytes == 0


def test_conteudo_com_exatamente_32_mib_cabe_no_limite(storage: FileStorage) -> None:
    _, size = storage.create_exclusive(
        "uploads/grande", repeated_chunks(MAX_FILE_BYTES), max_bytes=MAX_FILE_BYTES
    )

    assert size == MAX_FILE_BYTES
    assert storage.stat("uploads/grande").size_bytes == MAX_FILE_BYTES


def test_um_byte_alem_de_32_mib_aborta_sem_deixar_arquivo(storage: FileStorage) -> None:
    with pytest.raises(ContentTooLargeError):
        storage.create_exclusive(
            "uploads/grande", repeated_chunks(MAX_FILE_BYTES + 1), max_bytes=MAX_FILE_BYTES
        )

    assert files_under(storage.root) == []
    with pytest.raises(ObjectMissingError):
        storage.stat("uploads/grande")


def test_falha_do_iterador_no_meio_nao_deixa_arquivo(storage: FileStorage) -> None:
    with pytest.raises(ClientDisconnectedError):
        storage.create_exclusive("uploads/a", failing_after(b"metade"), max_bytes=100)

    assert files_under(storage.root) == []


def test_nunca_sobrescreve_chave_existente(storage: FileStorage) -> None:
    storage.create_exclusive("uploads/a", [b"original"], max_bytes=100)

    with pytest.raises(ObjectAlreadyExistsError):
        storage.create_exclusive("uploads/a", [b"intruso"], max_bytes=100)

    with storage.open("uploads/a") as stream:
        assert stream.read() == b"original"
    assert len(files_under(storage.root)) == 1


def test_excluir_remove_o_arquivo(storage: FileStorage) -> None:
    storage.create_exclusive("uploads/a", [b"x"], max_bytes=1)

    storage.delete("uploads/a")

    with pytest.raises(ObjectMissingError):
        storage.open("uploads/a")


def test_excluir_o_que_nao_existe_e_sucesso(storage: FileStorage) -> None:
    storage.delete("uploads/nunca-existiu")
    storage.delete("uploads/nunca-existiu")


def test_abrir_ou_consultar_chave_inexistente_falha(storage: FileStorage) -> None:
    with pytest.raises(ObjectMissingError):
        storage.open("uploads/x")
    with pytest.raises(ObjectMissingError):
        storage.stat("uploads/x")


def test_diretorios_e_arquivos_sao_privados_do_dono(storage: FileStorage) -> None:
    storage.create_exclusive("runs/r1/g1/saida", [b"x"], max_bytes=1)

    for directory in [storage.root, storage.root / "runs", storage.root / "runs/r1/g1"]:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((storage.root / "runs/r1/g1/saida").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../fora",
        "uploads/../../fora",
        "uploads/./a",
        "uploads//a",
        "uploads/a/",
        "uploads\\a",
        "uploads/a\x00b",
        ".tmp/a",
        "uploads/.oculto",
        "uploads/com espaço",
        "a" * 513,
    ],
)
def test_chave_invalida_e_recusada_antes_de_tocar_o_disco(storage: FileStorage, key: str) -> None:
    with pytest.raises(InvalidStorageKeyError):
        storage.create_exclusive(key, [b"x"], max_bytes=1)
    with pytest.raises(InvalidStorageKeyError):
        storage.delete(key)

    assert files_under(storage.root) == []


CUTOFF = 1_800_000_000.0
PART_NAME = f"{'a' * 32}.part"
OTHER_PART_NAME = f"{'b' * 32}.part"
THIRD_PART_NAME = f"{'c' * 32}.part"
NO_LIMIT = 500
NOTHING = TemporarySweep(removed=0, remaining=0, remaining_bytes=0)


def plant_temporary(
    storage: FileStorage, name: str, modified_at: float, content: bytes = b"abc"
) -> Path:
    directory = storage.root / ".tmp"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    os.utime(path, (modified_at, modified_at))
    return path


def test_varredura_sem_diretorio_de_temporarios_nao_faz_nada(tmp_path: Path) -> None:
    assert FileStorage(tmp_path).sweep_stale_temporaries(CUTOFF, NO_LIMIT) == NOTHING


def test_varredura_com_diretorio_vazio_nao_faz_nada(tmp_path: Path) -> None:
    (tmp_path / ".tmp").mkdir()

    assert FileStorage(tmp_path).sweep_stale_temporaries(CUTOFF, NO_LIMIT) == NOTHING


@pytest.mark.parametrize(
    ("modified_at", "removed"),
    [
        pytest.param(CUTOFF - 1, True, id="um-segundo-antes-do-corte"),
        pytest.param(CUTOFF, False, id="exatamente-no-corte"),
        pytest.param(CUTOFF + 1, False, id="depois-do-corte"),
    ],
)
def test_temporario_so_sai_quando_a_ultima_modificacao_e_anterior_ao_corte(
    tmp_path: Path, modified_at: float, removed: bool
) -> None:
    storage = FileStorage(tmp_path)
    part = plant_temporary(storage, PART_NAME, modified_at, b"abcd")

    sweep = storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT)

    assert part.exists() is not removed
    assert sweep == (
        TemporarySweep(removed=1, remaining=0, remaining_bytes=0)
        if removed
        else TemporarySweep(removed=0, remaining=1, remaining_bytes=4)
    )


def test_varredura_remove_todos_os_vencidos_e_mantem_e_mede_os_recentes(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path)
    stale = [
        plant_temporary(storage, PART_NAME, CUTOFF - 10),
        plant_temporary(storage, OTHER_PART_NAME, CUTOFF - 20),
    ]
    recent = plant_temporary(storage, THIRD_PART_NAME, CUTOFF + 10, b"12345")

    sweep = storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT)

    assert [path.exists() for path in stale] == [False, False]
    assert recent.exists()
    assert sweep == TemporarySweep(removed=2, remaining=1, remaining_bytes=5)


@pytest.mark.parametrize(
    ("limit", "removed", "remaining"),
    [
        pytest.param(0, 0, 3, id="teto-zero"),
        pytest.param(2, 2, 1, id="abaixo-do-total"),
        pytest.param(3, 3, 0, id="igual-ao-total"),
    ],
)
def test_varredura_remove_no_maximo_o_teto_e_conta_o_que_sobrou(
    tmp_path: Path, limit: int, removed: int, remaining: int
) -> None:
    storage = FileStorage(tmp_path)
    for name in (PART_NAME, OTHER_PART_NAME, THIRD_PART_NAME):
        plant_temporary(storage, name, CUTOFF - 10, b"ab")

    sweep = storage.sweep_stale_temporaries(CUTOFF, limit)

    left = list((tmp_path / ".tmp").iterdir())
    assert (sweep.removed, sweep.remaining, sweep.remaining_bytes) == (
        removed,
        remaining,
        remaining * 2,
    )
    assert len(left) == remaining


@pytest.mark.parametrize(
    "name", ["abc.part", ".part", f"{'a' * 32}.txt", f"{'A' * 32}.part", f"{'a' * 33}.part"]
)
def test_varredura_ignora_nome_que_nao_e_de_temporario_do_storage(
    tmp_path: Path, name: str
) -> None:
    storage = FileStorage(tmp_path)
    other = plant_temporary(storage, name, CUTOFF - 10)

    assert storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT) == NOTHING
    assert other.exists()


def test_varredura_nao_segue_symlink_nem_apaga_o_alvo(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "storage")
    target = tmp_path / "fora-do-storage"
    target.write_bytes(b"importante")
    os.utime(target, (CUTOFF - 10, CUTOFF - 10))
    directory = storage.root / ".tmp"
    directory.mkdir(parents=True)
    link = directory / PART_NAME
    link.symlink_to(target)
    os.utime(link, (CUTOFF - 10, CUTOFF - 10), follow_symlinks=False)

    assert storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT) == NOTHING
    assert link.is_symlink()
    assert target.read_bytes() == b"importante"


def test_varredura_nao_apaga_diretorio_com_nome_de_temporario(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path)
    directory = tmp_path / ".tmp" / PART_NAME
    directory.mkdir(parents=True)
    os.utime(directory, (CUTOFF - 10, CUTOFF - 10))

    assert storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT) == NOTHING
    assert directory.is_dir()


def test_varredura_nao_atinge_o_temporario_de_uma_gravacao_em_curso(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path)
    sweeps: list[TemporarySweep] = []

    def chunks_sweeping_midway() -> Iterator[bytes]:
        yield b"primeira parte"
        sweeps.append(storage.sweep_stale_temporaries(time.time() - 3600, NO_LIMIT))
        yield b"segunda parte"

    storage.create_exclusive("uploads/em-curso", chunks_sweeping_midway(), MAX_FILE_BYTES)

    assert sweeps[0].removed == 0
    assert sweeps[0].remaining == 1
    with storage.open("uploads/em-curso") as stored:
        assert stored.read() == b"primeira partesegunda parte"
