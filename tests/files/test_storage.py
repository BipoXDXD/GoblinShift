"""Storage em filesystem (docs/GoblinShiftPlan.md, 3.2 e 3.6), sem banco."""

import errno
import hashlib
import logging
import os
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from goblinshift.files.storage import (
    ContentTooLargeError,
    FileStorage,
    InvalidStorageKeyError,
    ObjectAlreadyExistsError,
    ObjectMissingError,
    ObjectTamperedError,
    StoredFile,
    TemporarySweep,
)
from tests.fifo_support import fail_if_blocked, make_fifo

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


def test_abrir_chave_que_e_symlink_falha_como_adulteracao_sem_expor_o_caminho(
    storage: FileStorage, tmp_path: Path
) -> None:
    # O_NOFOLLOW recusa o symlink com ELOOP; para quem chama isso é adulteração do storage, não
    # um OSError cru (o `str` dele traz o caminho no disco) nem ausência (o objeto existe).
    outside = tmp_path / "CANARY-alvo-fora-da-raiz"
    outside.write_bytes(b"segredo")
    link = storage.root / "uploads" / "alvo"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    with pytest.raises(ObjectTamperedError) as raised:
        storage.open("uploads/alvo")

    assert not isinstance(raised.value, ObjectMissingError)
    assert str(storage.root) not in str(raised.value)
    assert "CANARY" not in str(raised.value)
    assert link.is_symlink()


def test_abrir_chave_que_e_diretorio_falha_como_adulteracao_sem_expor_o_caminho(
    storage: FileStorage,
) -> None:
    # O `os.open` abre um diretório sem erro; sem checar o tipo, o `fdopen` falhava com
    # `IsADirectoryError` (cru, com o caminho no texto) e deixava o descritor aberto.
    directory = storage.root / "uploads" / "alvo"
    directory.mkdir(parents=True)

    with pytest.raises(ObjectTamperedError) as raised:
        storage.open("uploads/alvo")

    assert not isinstance(raised.value, ObjectMissingError)
    assert str(storage.root) not in str(raised.value)
    assert directory.is_dir()


def test_abrir_chave_que_e_fifo_falha_como_adulteracao_sem_bloquear(storage: FileStorage) -> None:
    # `open(O_RDONLY)` num FIFO sem escritor bloqueia antes do `fstat`: a thread do chamador
    # ficava presa. É o mesmo caso do symlink e do diretório: adulteração, sem expor o caminho.
    fifo = storage.root / "uploads" / "alvo"
    fifo.parent.mkdir(parents=True)
    make_fifo(fifo)

    with fail_if_blocked(fifo), pytest.raises(ObjectTamperedError) as raised:
        storage.open("uploads/alvo")

    assert not isinstance(raised.value, ObjectMissingError)
    assert str(storage.root) not in str(raised.value)
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


def test_abrir_fifo_nao_vaza_descritor(storage: FileStorage) -> None:
    fifo = storage.root / "uploads" / "alvo"
    fifo.parent.mkdir(parents=True)
    make_fifo(fifo)
    before = _open_fd_count()

    with fail_if_blocked(fifo):
        for _ in range(10):
            with pytest.raises(ObjectTamperedError):
                storage.open("uploads/alvo")

    assert _open_fd_count() == before


def _open_fd_count() -> int:
    return len(os.listdir("/dev/fd"))


@pytest.mark.parametrize("planted", ["diretorio", "symlink"])
def test_abrir_chave_adulterada_nao_vaza_descritor(
    storage: FileStorage, tmp_path: Path, planted: str
) -> None:
    tampered = storage.root / "uploads" / "alvo"
    tampered.parent.mkdir(parents=True)
    if planted == "diretorio":
        tampered.mkdir()
    else:
        tampered.symlink_to(tmp_path)
    before = _open_fd_count()

    for _ in range(10):
        with pytest.raises(ObjectTamperedError):
            storage.open("uploads/alvo")

    assert _open_fd_count() == before


def test_abrir_arquivo_comum_devolve_o_conteudo_e_nao_deixa_descritor_extra(
    storage: FileStorage,
) -> None:
    storage.create_exclusive("uploads/a", [b"abcd"], max_bytes=10)
    before = _open_fd_count()

    with storage.open("uploads/a") as file:
        assert file.read() == b"abcd"

    assert _open_fd_count() == before


def test_consultar_arquivo_comum_devolve_o_tamanho(storage: FileStorage) -> None:
    storage.create_exclusive("uploads/a", [b"abcd"], max_bytes=10)

    assert storage.stat("uploads/a").size_bytes == 4


@pytest.mark.parametrize("target", ["arquivo", "pendente"])
def test_consultar_chave_que_e_symlink_falha_como_adulteracao_sem_expor_o_caminho(
    storage: FileStorage, tmp_path: Path, target: str
) -> None:
    # Como `open`: o symlink no lugar do objeto é adulteração, nunca um arquivo presente. Vale
    # também para o link sem alvo, que o `lstat` enxerga como existente.
    outside = tmp_path / "CANARY-alvo-fora-da-raiz"
    if target == "arquivo":
        outside.write_bytes(b"segredo")
    link = storage.root / "uploads" / "alvo"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    with pytest.raises(ObjectTamperedError) as raised:
        storage.stat("uploads/alvo")

    assert not isinstance(raised.value, ObjectMissingError)
    assert str(storage.root) not in str(raised.value)
    assert "CANARY" not in str(raised.value)
    assert link.is_symlink()
    assert outside.exists() == (target == "arquivo")


def test_consultar_chave_que_e_diretorio_falha_como_adulteracao(storage: FileStorage) -> None:
    (storage.root / "uploads" / "alvo").mkdir(parents=True)

    with pytest.raises(ObjectTamperedError):
        storage.stat("uploads/alvo")


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
STORAGE_LOGGER = "goblinshift.files.storage"
REMOVAL_FAILED = "temporário não removido"


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
    "name",
    [
        "abc.part",
        ".part",
        f"{'a' * 32}.txt",
        f"{'A' * 32}.part",
        f"{'a' * 33}.part",
        f"{'a' * 32}.part.bak",
    ],
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


def fail_unlink(
    monkeypatch: pytest.MonkeyPatch, times: int, error_code: int = errno.EACCES
) -> None:
    """As `times` primeiras remoções falham com `error_code`.

    `OSError` escolhe a subclasse pelo errno: `EACCES` vira `PermissionError`, e `EROFS`, `EBUSY`
    e `EIO` ficam `OSError` puro.
    """
    original = Path.unlink
    denied = iter(range(times))

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if next(denied, None) is not None:
            raise OSError(error_code, "recusado", str(self))
        original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def plant_stale_parts(storage: FileStorage, content: bytes) -> None:
    for name in (PART_NAME, OTHER_PART_NAME, THIRD_PART_NAME):
        plant_temporary(storage, name, CUTOFF - 10, content)


def removal_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == REMOVAL_FAILED]


def test_falha_ao_apagar_um_temporario_nao_impede_os_demais_e_fica_registrada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    storage = FileStorage(tmp_path)
    plant_stale_parts(storage, b"ab")
    fail_unlink(monkeypatch, times=1)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        sweep = storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT)

    [survivor] = list((tmp_path / ".tmp").iterdir())
    [failure] = removal_failures(caplog)
    assert sweep == TemporarySweep(removed=2, remaining=1, remaining_bytes=2, failed=1)
    assert failure.__dict__["fields"] == {"tmp_part": survivor.name}
    assert failure.exc_info is not None
    assert failure.exc_info[0] is PermissionError


@pytest.mark.parametrize(
    "error_code",
    [errno.EROFS, errno.EBUSY, errno.EIO],
    ids=["disco-so-leitura", "arquivo-ocupado", "erro-de-entrada-e-saida"],
)
def test_erro_de_disco_ao_apagar_nao_para_a_varredura_e_fica_em_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_code: int,
) -> None:
    storage = FileStorage(tmp_path)
    plant_stale_parts(storage, b"ab")
    fail_unlink(monkeypatch, times=1, error_code=error_code)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        sweep = storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT)

    [failure] = removal_failures(caplog)
    assert sweep == TemporarySweep(removed=2, remaining=1, remaining_bytes=2, failed=1)
    assert failure.exc_info is not None
    error = failure.exc_info[1]
    assert type(error) is OSError
    assert error.errno == error_code


def test_teto_da_varredura_soma_remocoes_e_falhas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = FileStorage(tmp_path)
    plant_stale_parts(storage, b"ab")
    fail_unlink(monkeypatch, times=1)

    sweep = storage.sweep_stale_temporaries(CUTOFF, 2)

    assert sweep == TemporarySweep(removed=1, remaining=2, remaining_bytes=4, failed=1)
    assert len(list((tmp_path / ".tmp").iterdir())) == 2


def test_tentativas_que_falham_contam_no_teto_da_varredura(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    storage = FileStorage(tmp_path)
    plant_stale_parts(storage, b"ab")
    fail_unlink(monkeypatch, times=3)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        sweep = storage.sweep_stale_temporaries(CUTOFF, 2)

    assert sweep == TemporarySweep(removed=0, remaining=3, remaining_bytes=6, failed=2)
    assert len(removal_failures(caplog)) == 2
    assert len(list((tmp_path / ".tmp").iterdir())) == 3


def test_erro_ao_listar_o_diretorio_de_temporarios_continua_subindo(tmp_path: Path) -> None:
    # `.tmp` como arquivo: `scandir` levanta `NotADirectoryError` de verdade.
    (tmp_path / ".tmp").write_bytes(b"")

    with pytest.raises(NotADirectoryError):
        FileStorage(tmp_path).sweep_stale_temporaries(CUTOFF, NO_LIMIT)


def test_temporario_que_some_antes_do_unlink_conta_como_removido_e_nao_como_falha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    storage = FileStorage(tmp_path)
    plant_temporary(storage, PART_NAME, CUTOFF - 10)
    original = Path.unlink

    def unlink_after_another_process(self: Path, missing_ok: bool = False) -> None:
        os.unlink(self)
        original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_after_another_process)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        sweep = storage.sweep_stale_temporaries(CUTOFF, NO_LIMIT)

    assert sweep == TemporarySweep(removed=1, remaining=0, remaining_bytes=0, failed=0)
    assert removal_failures(caplog) == []


# --- Percurso do storage (relatório de órfãos) -----------------------------------------------


def test_walk_objects_lista_chave_tamanho_e_mtime(storage: FileStorage) -> None:
    storage.create_exclusive("uploads/a", [b"abc"], max_bytes=100)
    storage.create_exclusive("runs/r1/g1/b", [b"12345"], max_bytes=100)
    os.utime(storage.root / "uploads" / "a", (1_000_000, 1_000_000))

    found = {f.key: f for f in storage.walk_objects()}

    assert {key: f.size_bytes for key, f in found.items()} == {"uploads/a": 3, "runs/r1/g1/b": 5}
    assert found["uploads/a"].modified_at == 1_000_000
    assert isinstance(found["uploads/a"], StoredFile)


def test_walk_objects_ignora_tmp_sentinela_oculto_e_symlink(
    storage: FileStorage, tmp_path: Path
) -> None:
    storage.create_exclusive("uploads/a", [b"abc"], max_bytes=100)
    (storage.root / ".tmp").mkdir(exist_ok=True)
    (storage.root / ".tmp" / f"{'0' * 32}.part").write_bytes(b"parcial")
    (storage.root / ".goblinshift-storage").touch()
    (storage.root / ".oculto").write_bytes(b"x")
    (storage.root / "uploads" / ".escondido").write_bytes(b"x")
    (storage.root / "uploads" / "link").symlink_to(storage.root / "uploads" / "a")
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "alheio").write_bytes(b"x")
    (storage.root / "dirlink").symlink_to(outside, target_is_directory=True)

    assert [f.key for f in storage.walk_objects()] == ["uploads/a"]


def test_walk_objects_ignora_nome_fora_do_padrao_de_chave(storage: FileStorage) -> None:
    storage.create_exclusive("uploads/a", [b"abc"], max_bytes=100)
    (storage.root / "uploads" / "com espaço").write_bytes(b"x")
    (storage.root / "uploads" / "á").write_bytes(b"x")

    assert [f.key for f in storage.walk_objects()] == ["uploads/a"]


class _VanishingEntry:
    """Entrada de diretório cujo `stat` falha como se o arquivo tivesse sido expurgado."""

    def __init__(self, entry: os.DirEntry[str], vanish: set[str]) -> None:
        self._entry = entry
        self._vanish = vanish
        self.name = entry.name
        self.path = entry.path

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name in self._vanish:
            raise FileNotFoundError(self.path)
        return self._entry.stat(follow_symlinks=follow_symlinks)


class _VanishingScan:
    def __init__(self, scan: Iterator[os.DirEntry[str]], vanish: set[str]) -> None:
        self._scan = scan
        self._vanish = vanish

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        close = getattr(self._scan, "close", None)
        if close is not None:
            close()

    def __iter__(self) -> Iterator[_VanishingEntry]:
        # O que some vem primeiro: o percurso precisa seguir com o resto do diretório.
        for entry in sorted(self._scan, key=lambda e: e.name not in self._vanish):
            yield _VanishingEntry(entry, self._vanish)


def test_walk_objects_tolera_arquivo_que_some_no_meio(
    storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage.create_exclusive("uploads/some", [b"abc"], max_bytes=100)
    storage.create_exclusive("uploads/fica", [b"abc"], max_bytes=100)
    real_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda path: _VanishingScan(real_scandir(path), {"some"}))

    assert [f.key for f in storage.walk_objects()] == ["uploads/fica"]


def test_walk_objects_tolera_diretorio_que_some_no_meio(
    storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage.create_exclusive("uploads/a", [b"abc"], max_bytes=100)
    storage.create_exclusive("runs/b", [b"abc"], max_bytes=100)
    real_scandir = os.scandir

    def scandir(path: str | os.PathLike[str]) -> object:
        if Path(path).name == "runs":
            raise FileNotFoundError(path)
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)

    assert [f.key for f in storage.walk_objects()] == ["uploads/a"]


def test_walk_objects_sobe_erro_de_disco_que_nao_e_ausencia(
    storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage.create_exclusive("uploads/a", [b"abc"], max_bytes=100)

    def scandir(path: str | os.PathLike[str]) -> object:
        raise PermissionError(errno.EACCES, "negado", str(path))

    monkeypatch.setattr(os, "scandir", scandir)

    with pytest.raises(PermissionError):
        list(storage.walk_objects())


def test_walk_objects_com_raiz_inexistente_devolve_vazio(tmp_path: Path) -> None:
    assert list(FileStorage(tmp_path / "nao-existe").walk_objects()) == []


def test_walk_objects_e_preguicoso(storage: FileStorage) -> None:
    storage.create_exclusive("uploads/a", [b"abc"], max_bytes=100)

    assert isinstance(storage.walk_objects(), Iterator)
