"""`gs-admin orphan-files`: relatório somente leitura de arquivos órfãos do storage."""

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.admin_cli import (
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_USAGE,
    _orphan_line,
    main,
)
from goblinshift.files.orphans import OrphanFile, OrphanReason
from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from tests.files.support import create_user, mark_mounted, ready_upload

pytestmark = pytest.mark.integration

ONE_HOUR = 3600
LONG_AGO = 1_000_000  # 1970-01-12T13:46:40+00:00


@pytest.fixture(autouse=True)
def cli_environment(db_settings: Settings, engine: object) -> Iterator[None]:
    """A CLI lê as mesmas variáveis GS_* que a API; `engine` deixa o banco limpo."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GS_DB_HOST", db_settings.db_host)
        mp.setenv("GS_DB_PORT", str(db_settings.db_port))
        mp.setenv("GS_DB_USER", db_settings.db_user)
        mp.setenv("GS_DB_PASSWORD", db_settings.db_password.get_secret_value())
        mp.setenv("GS_DB_NAME", db_settings.db_name)
        yield


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FileStorage:
    root = tmp_path / "storage"
    monkeypatch.setenv("GS_STORAGE_ROOT", str(root))
    storage = FileStorage(root)
    mark_mounted(storage)
    return storage


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    exit_code = main(["orphan-files", *args])
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def put(storage: FileStorage, key: str, content: bytes, mtime: float | None = None) -> None:
    storage.create_exclusive(key, [content], max_bytes=1024)
    if mtime is not None:
        os.utime(storage.root.joinpath(*key.split("/")), (mtime, mtime))


def files_under(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def row_summary(factory: sessionmaker[Session]) -> tuple[int, str | None]:
    with factory() as session:
        row = session.execute(select(func.count(), func.max(stored_object.c.state))).one()
    return row[0], row[1]


def test_lista_orfao_em_tsv_e_resume_em_stderr(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    ready_upload(factory, storage, create_user(factory, "ana"))
    put(storage, "uploads/solto", b"12345", mtime=LONG_AGO)

    exit_code, out, err = run(capsys)

    assert exit_code == EXIT_OK
    assert out == "uploads/solto\t5\t1970-01-12T13:46:40+00:00\tsem-linha\n"
    assert "gs-admin: 1 órfãos sem linha, 5 bytes" in err
    assert "gs-admin: 0 marcados como removidos mas ainda presentes, 0 bytes" in err


def test_linha_marcada_removida_com_arquivo_sai_em_categoria_propria(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    ready_upload(factory, storage, create_user(factory, "ana"), b"abcd")
    with unit_of_work(factory) as session:
        session.execute(update(stored_object).values(state=ObjectState.MISSING))
    [key] = files_under(storage.root / "uploads")
    os.utime(storage.root / "uploads" / key, (LONG_AGO, LONG_AGO))

    exit_code, out, err = run(capsys)

    assert exit_code == EXIT_OK
    assert out == f"uploads/{key}\t4\t1970-01-12T13:46:40+00:00\tmarcado-removido-mas-presente\n"
    assert "gs-admin: 0 órfãos sem linha, 0 bytes" in err
    assert "gs-admin: 1 marcados como removidos mas ainda presentes, 4 bytes" in err


def test_sem_orfaos_sai_zero_sem_linhas(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    ready_upload(factory, storage, create_user(factory, "ana"))

    exit_code, out, err = run(capsys, "--min-age-hours", "1")

    assert (exit_code, out) == (EXIT_OK, "")
    assert "gs-admin: 0 órfãos sem linha, 0 bytes" in err


def test_padrao_de_6_horas_poupa_arquivo_de_5h_e_lista_o_de_7h(
    capsys: pytest.CaptureFixture[str], storage: FileStorage
) -> None:
    now = time.time()
    put(storage, "uploads/cinco", b"a", mtime=now - 5 * ONE_HOUR)
    put(storage, "uploads/sete", b"a", mtime=now - 7 * ONE_HOUR)

    exit_code, out, _ = run(capsys)

    assert exit_code == EXIT_OK
    assert [line.split("\t")[0] for line in out.splitlines()] == ["uploads/sete"]


def test_min_age_hours_muda_o_corte(
    capsys: pytest.CaptureFixture[str], storage: FileStorage
) -> None:
    put(storage, "uploads/de_5h", b"a", mtime=time.time() - 5 * ONE_HOUR)

    _, out, _ = run(capsys, "--min-age-hours", "4")

    assert [line.split("\t")[0] for line in out.splitlines()] == ["uploads/de_5h"]


def test_limit_trunca_a_listagem_e_avisa_sem_perder_o_total(
    capsys: pytest.CaptureFixture[str], storage: FileStorage
) -> None:
    for index in range(3):
        put(storage, f"uploads/o{index}", b"xx", mtime=LONG_AGO)

    exit_code, out, err = run(capsys, "--limit", "2")

    assert exit_code == EXIT_OK
    assert len(out.splitlines()) == 2
    assert "gs-admin: 3 órfãos sem linha, 6 bytes" in err
    assert "gs-admin: listagem truncada em 2; os totais acima contam tudo" in err


def test_nome_fora_do_padrao_no_disco_nao_quebra_o_tsv(
    capsys: pytest.CaptureFixture[str], storage: FileStorage
) -> None:
    put(storage, "uploads/a.b-c", b"x", mtime=LONG_AGO)
    (storage.root / "uploads" / "com\ttab e espaço").write_bytes(b"x")
    os.utime(storage.root / "uploads" / "com\ttab e espaço", (LONG_AGO, LONG_AGO))

    exit_code, out, _ = run(capsys)

    assert exit_code == EXIT_OK
    assert out == "uploads/a.b-c\t1\t1970-01-12T13:46:40+00:00\tsem-linha\n"


def test_chave_com_controle_ou_nao_ascii_sai_escapada_numa_linha() -> None:
    file = OrphanFile("a\tb\ncá", 7, float(LONG_AGO), OrphanReason.NO_ROW)

    assert _orphan_line(file) == "a\\tb\\nc\\xe1\t7\t1970-01-12T13:46:40+00:00\tsem-linha"


def test_storage_desmontado_sai_com_4_e_mensagem(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "desmontado"
    monkeypatch.setenv("GS_STORAGE_ROOT", str(root))
    put(FileStorage(root), "uploads/a", b"x", mtime=LONG_AGO)

    exit_code, out, err = run(capsys)

    assert (exit_code, out) == (EXIT_FAILURE, "")
    assert err == "gs-admin: storage não montado; nada foi verificado\n"


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
def test_min_age_hours_invalido_e_erro_de_uso(value: str) -> None:
    with pytest.raises(SystemExit) as usage_error:
        main(["orphan-files", "--min-age-hours", value])

    assert usage_error.value.code == EXIT_USAGE


@pytest.mark.parametrize("value", ["0", "-5"])
def test_limit_invalido_e_erro_de_uso(value: str) -> None:
    with pytest.raises(SystemExit) as usage_error:
        main(["orphan-files", "--limit", value])

    assert usage_error.value.code == EXIT_USAGE


def test_somente_leitura(
    capsys: pytest.CaptureFixture[str], factory: sessionmaker[Session], storage: FileStorage
) -> None:
    ready_upload(factory, storage, create_user(factory, "ana"))
    with unit_of_work(factory) as session:
        session.execute(update(stored_object).values(state=ObjectState.DELETED))
    put(storage, "uploads/solto", b"x", mtime=LONG_AGO)
    for path in storage.root.joinpath("uploads").iterdir():
        os.utime(path, (LONG_AGO, LONG_AGO))
    before = (files_under(storage.root), row_summary(factory))

    exit_code, out, _ = run(capsys)

    assert exit_code == EXIT_OK
    assert len(out.splitlines()) == 2
    assert (files_under(storage.root), row_summary(factory)) == before
