"""Relatório de arquivos órfãos no disco: o storage contra `stored_object`, só leitura."""

import os
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.orphans import (
    OrphanFile,
    OrphanReason,
    OrphanReport,
    StorageNotMountedError,
    find_orphan_files,
)
from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object, upload
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_intent, create_user, mark_mounted, ready_upload

pytestmark = pytest.mark.integration

ONE_HOUR = 3600
LIST_LIMIT = 200


@pytest.fixture(autouse=True)
def mounted(storage: FileStorage) -> None:
    mark_mounted(storage)


def future_cutoff() -> float:
    """Corte que deixa todo arquivo recém-criado dentro do relatório."""
    return time.time() + ONE_HOUR


def put(storage: FileStorage, key: str, content: bytes = b"abc") -> None:
    storage.create_exclusive(key, [content], max_bytes=1024)


def age(storage: FileStorage, key: str, mtime: float) -> None:
    os.utime(storage.root.joinpath(*key.split("/")), (mtime, mtime))


def key_of(factory: sessionmaker[Session], upload_id: uuid.UUID) -> str:
    with factory() as session:
        key: str = session.execute(
            select(stored_object.c.storage_key)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == upload_id)
        ).scalar_one()
    return key


def set_state(factory: sessionmaker[Session], key: str, state: ObjectState) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(stored_object).where(stored_object.c.storage_key == key).values(state=state)
        )


def report_of(
    factory: sessionmaker[Session],
    storage: FileStorage,
    *,
    modified_before: float | None = None,
    list_limit: int = LIST_LIMIT,
    batch_size: int = 1000,
) -> OrphanReport:
    with factory() as session:
        return find_orphan_files(
            session,
            storage,
            modified_before=future_cutoff() if modified_before is None else modified_before,
            list_limit=list_limit,
            batch_size=batch_size,
        )


def test_arquivo_sem_linha_e_orfao_puro(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, "uploads/solto", b"12345")

    report = report_of(factory, storage)

    assert report.orphan_count == 1
    assert report.orphan_bytes == 5
    assert report.marked_removed_count == 0
    assert [(f.key, f.size_bytes, f.reason) for f in report.listed] == [
        ("uploads/solto", 5, OrphanReason.NO_ROW)
    ]
    assert OrphanReason.NO_ROW.value == "sem-linha"


def test_objeto_committed_nao_e_orfao(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = create_user(factory, "ana")
    ready_upload(factory, storage, owner)

    report = report_of(factory, storage)

    assert (report.orphan_count, report.marked_removed_count, report.listed) == (0, 0, ())


def test_pending_e_delete_requested_com_arquivo_nao_sao_orfaos(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    pending = key_of(factory, create_intent(factory, owner).id)
    put(storage, pending)
    requested = key_of(factory, ready_upload(factory, storage, owner).id)
    set_state(factory, requested, ObjectState.DELETE_REQUESTED)

    report = report_of(factory, storage)

    assert (report.orphan_count, report.marked_removed_count) == (0, 0)


def test_linha_deleted_ou_missing_com_arquivo_vai_para_categoria_propria(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    deleted = key_of(factory, ready_upload(factory, storage, owner, b"aaaa").id)
    missing = key_of(factory, ready_upload(factory, storage, owner, b"bb").id)
    set_state(factory, deleted, ObjectState.DELETED)
    set_state(factory, missing, ObjectState.MISSING)
    put(storage, "uploads/solto", b"c")

    report = report_of(factory, storage)

    assert (report.orphan_count, report.orphan_bytes) == (1, 1)
    assert (report.marked_removed_count, report.marked_removed_bytes) == (2, 6)
    reasons = {f.key: f.reason for f in report.listed}
    assert reasons == {
        deleted: OrphanReason.MARKED_REMOVED,
        missing: OrphanReason.MARKED_REMOVED,
        "uploads/solto": OrphanReason.NO_ROW,
    }
    assert OrphanReason.MARKED_REMOVED.value == "marcado-removido-mas-presente"


def test_corte_de_idade_exclui_o_arquivo_no_ponto_do_corte_e_depois_dele(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    cutoff = 2_000_000
    for name, mtime in (("antes", cutoff - 1), ("no_corte", cutoff), ("depois", cutoff + 1)):
        put(storage, f"uploads/{name}")
        age(storage, f"uploads/{name}", mtime)

    report = report_of(factory, storage, modified_before=cutoff)

    assert [f.key for f in report.listed] == ["uploads/antes"]
    assert report.orphan_count == 1


def test_arquivo_novo_com_linha_ainda_nao_confirmada_nao_entra(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, "uploads/gravando")  # no disco agora, sem linha: gravação em curso
    old = time.time() - 7 * ONE_HOUR
    put(storage, "uploads/velho")
    age(storage, "uploads/velho", old)

    report = report_of(factory, storage, modified_before=time.time() - 6 * ONE_HOUR)

    assert [f.key for f in report.listed] == ["uploads/velho"]


def test_listagem_limitada_mantem_contagens_e_bytes_totais(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    for index in range(5):
        put(storage, f"uploads/o{index}", b"x" * (index + 1))

    report = report_of(factory, storage, list_limit=2)

    assert len(report.listed) == 2
    assert report.truncated is True
    assert report.orphan_count == 5
    assert report.orphan_bytes == 15


def test_listagem_dentro_do_limite_nao_e_truncada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, "uploads/a")
    put(storage, "uploads/b")

    report = report_of(factory, storage, list_limit=2)

    assert (len(report.listed), report.truncated) == (2, False)


def test_lotes_pequenos_dao_o_mesmo_resultado(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "ana")
    for _ in range(3):
        ready_upload(factory, storage, owner)
    gone = key_of(factory, ready_upload(factory, storage, owner, b"zz").id)
    set_state(factory, gone, ObjectState.DELETED)
    for index in range(5):
        put(storage, f"uploads/o{index}", b"yy")

    small = report_of(factory, storage, batch_size=2)
    large = report_of(factory, storage, batch_size=1000)

    assert (small.orphan_count, small.orphan_bytes) == (5, 10)
    assert (small.marked_removed_count, small.marked_removed_bytes) == (1, 2)
    assert sorted(small.listed, key=_by_key) == sorted(large.listed, key=_by_key)
    assert (small.orphan_count, small.marked_removed_count) == (
        large.orphan_count,
        large.marked_removed_count,
    )


def _by_key(file: OrphanFile) -> str:
    return file.key


def test_storage_desmontado_levanta_e_nao_consulta_o_banco(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    unmounted = FileStorage(tmp_path / "desmontado")
    put(unmounted, "uploads/a")  # a raiz tem conteúdo, mas falta a sentinela
    with factory() as session:
        engine = session.get_bind()
        assert isinstance(engine, Engine)
        statements: list[str] = []

        def record(*args: object) -> None:
            statements.append(str(args[2]))

        event.listen(engine, "before_cursor_execute", record)
        try:
            with pytest.raises(StorageNotMountedError):
                find_orphan_files(
                    session, unmounted, modified_before=future_cutoff(), list_limit=LIST_LIMIT
                )
        finally:
            event.remove(engine, "before_cursor_execute", record)
    assert statements == []


def test_nao_altera_disco_nem_banco(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = create_user(factory, "ana")
    gone = key_of(factory, ready_upload(factory, storage, owner).id)
    set_state(factory, gone, ObjectState.MISSING)
    put(storage, "uploads/solto")
    before = (_files(storage), _row_summary(factory))

    report_of(factory, storage)

    assert (_files(storage), _row_summary(factory)) == before


def _files(storage: FileStorage) -> list[str]:
    return sorted(str(p.relative_to(storage.root)) for p in storage.root.rglob("*") if p.is_file())


def _row_summary(factory: sessionmaker[Session]) -> tuple[int, str | None]:
    with factory() as session:
        row = session.execute(select(func.count(), func.max(stored_object.c.state))).one()
    return row[0], row[1]


def test_nao_segura_transacao_aberta_enquanto_percorre_o_disco(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    """O banco derruba transação ociosa (30 s); cada lote é uma transação curta."""
    for index in range(3):
        put(storage, f"uploads/o{index}")

    with factory() as session:
        find_orphan_files(
            session, storage, modified_before=future_cutoff(), list_limit=1, batch_size=1
        )

        assert not session.in_transaction()
