"""Arquivos órfãos no disco contra `stored_object`: o relatório (só leitura) e a remoção."""

import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Executable, Select, event, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files import orphans
from goblinshift.files.orphans import (
    DeletionOutcome,
    OrphanDeletion,
    OrphanFile,
    OrphanReason,
    OrphanReport,
    StorageNotMountedError,
    delete_orphan_files,
    find_orphan_files,
)
from goblinshift.files.states import ObjectState
from goblinshift.files.storage import MOUNT_MARKER, FileStorage, StoredFile
from goblinshift.files.tables import stored_object, upload
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_intent, create_user, mark_mounted, ready_upload

pytestmark = pytest.mark.integration

ONE_HOUR = 3600
LIST_LIMIT = 200


def k(label: str) -> str:
    """Chave de upload no formato da aplicação (`uploads/<32 hex>`), legível no teste."""
    return f"uploads/{label.encode().hex().ljust(32, '0')}"


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
    put(storage, k("solto"), b"12345")

    report = report_of(factory, storage)

    assert report.orphan_count == 1
    assert report.orphan_bytes == 5
    assert report.marked_removed_count == 0
    assert [(f.key, f.size_bytes, f.reason) for f in report.listed] == [
        (k("solto"), 5, OrphanReason.NO_ROW)
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
    put(storage, k("solto"), b"c")

    report = report_of(factory, storage)

    assert (report.orphan_count, report.orphan_bytes) == (1, 1)
    assert (report.marked_removed_count, report.marked_removed_bytes) == (2, 6)
    reasons = {f.key: f.reason for f in report.listed}
    assert reasons == {
        deleted: OrphanReason.MARKED_REMOVED,
        missing: OrphanReason.MARKED_REMOVED,
        k("solto"): OrphanReason.NO_ROW,
    }
    assert OrphanReason.MARKED_REMOVED.value == "marcado-removido-mas-presente"


def test_corte_de_idade_exclui_o_arquivo_no_ponto_do_corte_e_depois_dele(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    cutoff = 2_000_000
    for name, mtime in (("antes", cutoff - 1), ("no_corte", cutoff), ("depois", cutoff + 1)):
        put(storage, k(name))
        age(storage, k(name), mtime)

    report = report_of(factory, storage, modified_before=cutoff)

    assert [f.key for f in report.listed] == [k("antes")]
    assert report.orphan_count == 1


def test_arquivo_novo_com_linha_ainda_nao_confirmada_nao_entra(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, k("gravando"))  # no disco agora, sem linha: gravação em curso
    old = time.time() - 7 * ONE_HOUR
    put(storage, k("velho"))
    age(storage, k("velho"), old)

    report = report_of(factory, storage, modified_before=time.time() - 6 * ONE_HOUR)

    assert [f.key for f in report.listed] == [k("velho")]


def test_listagem_limitada_mantem_contagens_e_bytes_totais(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    for index in range(5):
        put(storage, k(f"o{index}"), b"x" * (index + 1))

    report = report_of(factory, storage, list_limit=2)

    assert len(report.listed) == 2
    assert report.truncated is True
    assert report.orphan_count == 5
    assert report.orphan_bytes == 15


def test_listagem_dentro_do_limite_nao_e_truncada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, k("a"))
    put(storage, k("b"))

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
        put(storage, k(f"o{index}"), b"yy")

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
    put(unmounted, k("a"))  # a raiz tem conteúdo, mas falta a sentinela
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
    put(storage, k("solto"))
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
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O banco derruba transação ociosa (30 s): entre um lote e o próximo não há transação."""
    for index in range(3):
        put(storage, k(f"o{index}"))
    in_transaction_when_disk_is_read: list[bool] = []

    with factory() as session:
        real_walk = storage.walk_objects

        def watched_walk() -> Iterator[StoredFile]:
            for found in real_walk():
                in_transaction_when_disk_is_read.append(session.in_transaction())
                yield found

        monkeypatch.setattr(storage, "walk_objects", watched_walk)

        report = find_orphan_files(
            session, storage, modified_before=future_cutoff(), list_limit=1, batch_size=1
        )

        assert report.orphan_count == 3
        assert in_transaction_when_disk_is_read == [False, False, False]
        assert not session.in_transaction()


def test_classify_roda_em_transacao_read_only(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O "só leitura" é garantido pelo banco: escrever dentro da transação do lote falha."""
    put(storage, k("solto"))
    sqlstates: list[str | None] = []

    with factory() as session:
        real_execute = session.execute

        def execute_then_try_write(statement: Executable, *args: Any, **kwargs: Any) -> Any:
            result = real_execute(statement, *args, **kwargs)
            if isinstance(statement, Select) and not sqlstates:
                try:
                    with session.begin_nested():
                        real_execute(update(stored_object).values(state=stored_object.c.state))
                    sqlstates.append(None)
                except DBAPIError as error:
                    sqlstates.append(getattr(error.orig, "sqlstate", None))
            return result

        monkeypatch.setattr(session, "execute", execute_then_try_write)

        report = find_orphan_files(
            session, storage, modified_before=future_cutoff(), list_limit=LIST_LIMIT
        )

    assert sqlstates == ["25006"]
    assert report.orphan_count == 1


def deletion_of(
    factory: sessionmaker[Session],
    storage: FileStorage,
    *,
    modified_before: float,
    limit: int = LIST_LIMIT,
) -> OrphanDeletion:
    with factory() as session:
        return delete_orphan_files(session, storage, modified_before=modified_before, limit=limit)


def test_delete_orphans_apaga_sem_linha_antigo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, k("velho"), b"12345")

    result = deletion_of(factory, storage, modified_before=future_cutoff())

    assert [(f.key, f.size_bytes, f.reason) for f in result.deleted] == [
        (k("velho"), 5, OrphanReason.NO_ROW)
    ]
    assert _object_files(storage) == []


def test_delete_orphans_apaga_candidato_de_run_sem_linha(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    candidate = f"runs/{uuid.uuid4()}/g12/{uuid.uuid4().hex}"
    put(storage, candidate)

    result = deletion_of(factory, storage, modified_before=future_cutoff())

    assert [f.key for f in result.deleted] == [candidate]


def test_delete_orphans_poupa_sem_linha_recente(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, k("recente"))

    result = deletion_of(factory, storage, modified_before=time.time() - ONE_HOUR)

    assert (result.deleted, _object_files(storage)) == ((), [k("recente")])


@pytest.mark.parametrize("state", [ObjectState.DELETED, ObjectState.MISSING, ObjectState.COMMITTED])
def test_delete_orphans_poupa_arquivo_com_linha(
    factory: sessionmaker[Session], storage: FileStorage, state: ObjectState
) -> None:
    owner = create_user(factory, "ana")
    key = key_of(factory, ready_upload(factory, storage, owner).id)
    set_state(factory, key, state)

    result = deletion_of(factory, storage, modified_before=future_cutoff())

    assert (result.deleted, _object_files(storage)) == ((), [key])


@pytest.mark.parametrize(
    "name",
    ["restore-notes.txt", "dump.sql", "uploads/solto", "uploads/ABCDEF0123456789ABCDEF0123456789"],
)
def test_nome_fora_dos_formatos_da_aplicacao_e_desconhecido_e_nunca_apagado(
    factory: sessionmaker[Session], storage: FileStorage, name: str
) -> None:
    put(storage, name)

    report = report_of(factory, storage)
    result = deletion_of(factory, storage, modified_before=future_cutoff())

    assert [(f.key, f.reason) for f in report.listed] == [(name, OrphanReason.UNKNOWN)]
    assert (report.unknown_count, report.orphan_count) == (1, 0)
    assert result.deleted == ()
    assert _object_files(storage) == [name]


def _object_files(storage: FileStorage) -> list[str]:
    return [f for f in _files(storage) if f != MOUNT_MARKER]


def test_linha_criada_entre_a_lista_e_a_remocao_salva_o_arquivo(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = create_user(factory, "ana")
    put(storage, k("restaurado"))
    put(storage, k("solto"))
    real_classify = orphans._classify

    def classify_then_insert_row(
        session: Session, batch: Iterator[StoredFile]
    ) -> Iterator[tuple[StoredFile, OrphanReason]]:
        results = list(real_classify(session, batch))
        with unit_of_work(factory) as other:
            other.execute(insert(stored_object).values(owner_id=owner, storage_key=k("restaurado")))
        yield from results

    monkeypatch.setattr(orphans, "_classify", classify_then_insert_row)

    result = deletion_of(factory, storage, modified_before=future_cutoff())

    assert [f.key for f in result.deleted] == [k("solto")]
    assert result.skipped == 1
    assert k("restaurado") in _files(storage)


def test_arquivo_tocado_entre_a_lista_e_a_remocao_fica(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    cutoff = 2_000_000
    put(storage, k("regravado"))
    age(storage, k("regravado"), cutoff - 1)
    real_classify = orphans._classify

    def classify_then_touch(
        session: Session, batch: Iterator[StoredFile]
    ) -> Iterator[tuple[StoredFile, OrphanReason]]:
        results = list(real_classify(session, batch))
        age(storage, k("regravado"), cutoff + 1)
        yield from results

    monkeypatch.setattr(orphans, "_classify", classify_then_touch)

    result = deletion_of(factory, storage, modified_before=cutoff)

    assert (result.deleted, result.skipped) == ((), 1)
    assert k("regravado") in _files(storage)


def test_delete_orphans_respeita_o_limit(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    for index in range(5):
        put(storage, k(f"o{index}"))

    result = deletion_of(factory, storage, modified_before=future_cutoff(), limit=2)

    assert len(result.deleted) == 2
    assert len([f for f in _files(storage) if f.startswith("uploads/")]) == 3


def test_delete_orphans_com_storage_desmontado_levanta(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    unmounted = FileStorage(tmp_path / "desmontado")
    put(unmounted, k("a"))

    with pytest.raises(StorageNotMountedError):
        deletion_of(factory, unmounted, modified_before=future_cutoff())

    assert tmp_path.joinpath("desmontado", *k("a").split("/")).exists()


def test_cada_remocao_e_avisada_na_hora_mesmo_se_a_seguinte_quebra(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(4):
        put(storage, k(f"o{index}"))
    real_delete = storage.delete
    calls: list[str] = []

    def delete_then_break(key: str) -> None:
        calls.append(key)
        if len(calls) == 3:
            raise RuntimeError("queda no meio")
        real_delete(key)

    monkeypatch.setattr(storage, "delete", delete_then_break)
    seen: list[tuple[str, DeletionOutcome]] = []

    with pytest.raises(RuntimeError), factory() as session:
        delete_orphan_files(
            session,
            storage,
            modified_before=future_cutoff(),
            limit=LIST_LIMIT,
            on_outcome=lambda file, outcome: seen.append((file.key, outcome)),
        )

    assert seen == [(key, DeletionOutcome.DELETED) for key in calls[:2]]


def test_falha_ao_apagar_um_arquivo_nao_para_os_outros(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a", "b", "c"):
        put(storage, k(name))
    real_delete = storage.delete

    def delete_failing_b(key: str) -> None:
        if key == k("b"):
            raise PermissionError(13, "Permission denied")
        real_delete(key)

    monkeypatch.setattr(storage, "delete", delete_failing_b)

    result = deletion_of(factory, storage, modified_before=future_cutoff())

    assert result.failed == (k("b"),)
    assert sorted(f.key for f in result.deleted) == [k("a"), k("c")]
    assert _object_files(storage) == [k("b")]


def test_adulterado_e_sumido_antes_da_remocao_sao_contados_a_parte(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(storage, k("adulterado"))
    put(storage, k("sumido"))
    real_classify = orphans._classify

    def classify_then_tamper(
        session: Session, batch: Iterator[StoredFile]
    ) -> Iterator[tuple[StoredFile, OrphanReason]]:
        results = list(real_classify(session, batch))
        tampered = storage.root.joinpath(*k("adulterado").split("/"))
        tampered.unlink()
        tampered.symlink_to(storage.root / MOUNT_MARKER)
        storage.root.joinpath(*k("sumido").split("/")).unlink()
        yield from results

    monkeypatch.setattr(orphans, "_classify", classify_then_tamper)

    result = deletion_of(factory, storage, modified_before=future_cutoff())

    assert (result.deleted, result.skipped) == ((), 0)
    assert result.tampered == (k("adulterado"),)
    assert result.vanished == (k("sumido"),)
    assert storage.root.joinpath(*k("adulterado").split("/")).is_symlink()


def test_limite_exato_sem_sobra_nao_e_truncado(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    put(storage, k("a"))
    put(storage, k("b"))

    result = deletion_of(factory, storage, modified_before=future_cutoff(), limit=2)

    assert (len(result.deleted), result.truncated) == (2, False)


def test_limite_com_sobra_e_truncado(factory: sessionmaker[Session], storage: FileStorage) -> None:
    for name in ("a", "b", "c"):
        put(storage, k(name))

    result = deletion_of(factory, storage, modified_before=future_cutoff(), limit=2)

    assert (len(result.deleted), result.truncated) == (2, True)
