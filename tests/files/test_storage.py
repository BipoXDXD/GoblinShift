"""Storage em filesystem (docs/GoblinShiftPlan.md, 3.2 e 3.6), sem banco."""

import hashlib
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from goblinshift.files.storage import (
    ContentTooLargeError,
    FileStorage,
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectMissingError,
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
