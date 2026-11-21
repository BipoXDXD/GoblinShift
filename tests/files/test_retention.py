"""Expiração em lote e remoção física com limitador (1.7 e 3.4)."""

import errno
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift import capacity
from goblinshift.files import retention
from goblinshift.files.artifacts import get_artifact, open_for_download, publish_artifact
from goblinshift.files.errors import UploadGoneError, UploadNotFoundError
from goblinshift.files.limits import INCOMPLETE_UPLOAD_TTL, MIN_INTENT_RESERVED_BYTES
from goblinshift.files.objects import hold, release
from goblinshift.files.retention import (
    expire_artifacts,
    expire_incomplete_uploads,
    expire_unused_uploads,
    next_storage_release_in,
    purge_deleted_objects,
)
from goblinshift.files.states import ArtifactRole, Availability, HolderKind, UploadState
from goblinshift.files.storage import FileStorage, ObjectMissingError
from goblinshift.files.tables import artifact, stored_object, upload
from goblinshift.files.uploads import (
    UploadView,
    get_upload,
    lock_ready_uploads,
    receive_content,
    request_deletion,
)
from goblinshift.platform.db import unit_of_work
from tests.capacity.support import account_reserved, live_amount
from tests.files.support import (
    create_intent,
    create_user,
    pass_expiry_after_deletion,
    ready_upload,
)

pytestmark = pytest.mark.integration

GIB = 1024 * 1024 * 1024


def expire_row(factory: sessionmaker[Session], table: Table, row_id: uuid.UUID) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(table)
            .where(table.c.id == row_id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )


def object_of(factory: sessionmaker[Session], upload_id: uuid.UUID) -> tuple[str, str]:
    with factory() as session:
        row = session.execute(
            select(stored_object.c.state, stored_object.c.storage_key)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == upload_id)
        ).one()
    return row.state, row.storage_key


def object_states(factory: sessionmaker[Session]) -> dict[str, int]:
    with factory() as session:
        rows = session.execute(
            select(stored_object.c.state, func.count()).group_by(stored_object.c.state)
        ).all()
    return {state: count for state, count in rows}


def seed_delete_requested(
    factory: sessionmaker[Session], owner: uuid.UUID, count: int, size_bytes: int
) -> None:
    """Objetos baratos já pedidos para exclusão, só com a linha (remover arquivo ausente é ok)."""
    with unit_of_work(factory) as session:
        session.execute(
            insert(stored_object).values(
                state="delete_requested",
                sha256=b"\x00" * 32,
                delete_requested_at=func.now(),
            ),
            [
                {"owner_id": owner, "storage_key": f"seed/{i}", "size_bytes": size_bytes}
                for i in range(count)
            ],
        )


def ready_in_use(
    factory: sessionmaker[Session], storage: FileStorage, owner: uuid.UUID, run_id: uuid.UUID
) -> UploadView:
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        hold(session, [ready.object_id], HolderKind.RUN, run_id)
    return view


# --- Uploads incompletos ----------------------------------------------------------------------


def test_upload_incompleto_vencido_perde_linha_e_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    stale = create_intent(factory, owner)
    fresh = create_intent(factory, owner)
    expire_row(factory, upload, stale.id)

    with factory() as session:
        assert expire_incomplete_uploads(session, storage).uploads == 1

    with pytest.raises(UploadNotFoundError), factory() as session:
        get_upload(session, owner, stale.id)
    with factory() as session:
        assert get_upload(session, owner, fresh.id).deleted_at is None
    assert object_states(factory) == {"pending": 1}


def test_falha_ao_apagar_um_incompleto_nao_perde_os_demais(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = create_user(factory, "a")
    for intent in (create_intent(factory, owner), create_intent(factory, owner)):
        expire_row(factory, upload, intent.id)
    attempted: list[str] = []

    def refuse_first(key: str) -> None:
        attempted.append(key)
        if len(attempted) == 1:
            raise PermissionError("disco recusou")

    monkeypatch.setattr(storage, "delete", refuse_first)

    with factory() as session:
        report = expire_incomplete_uploads(session, storage)

    assert report.uploads == 2
    assert len(set(attempted)) == 2
    # A linha já saiu: o relatório é o único jeito de quem chama contar o que ficou no disco.
    assert report.failed_keys == (attempted[0],)


def test_upload_incompleto_excluido_sai_sem_esperar_o_prazo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, intent.id)

    with factory() as session:
        assert expire_incomplete_uploads(session, storage).uploads == 1

    assert object_states(factory) == {}


# Dois blocos de 2500 bytes: o escritor fica preso entre eles, com o primeiro já no disco.
HALF_CONTENT_BYTES = 2500
CONTENT_BYTES = 2 * HALF_CONTENT_BYTES
WRITER_TIMEOUT_S = 10


@dataclass
class BlockedWriter:
    first_chunk_sent: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    error: BaseException | None = None


class FakeClock:
    """Relógio monotônico do escritor, que só anda quando o teste manda."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, by: timedelta) -> None:
        self._now += by.total_seconds()


@contextmanager
def blocked_writer(
    factory: sessionmaker[Session],
    storage: FileStorage,
    owner: uuid.UUID,
    upload_id: uuid.UUID,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[BlockedWriter]:
    """Escritor numa thread, preso no meio da transferência até o bloco `with` terminar."""
    writer = BlockedWriter()

    def chunks() -> Iterator[bytes]:
        yield b"a" * HALF_CONTENT_BYTES
        writer.first_chunk_sent.set()
        writer.release.wait(timeout=WRITER_TIMEOUT_S)
        yield b"b" * HALF_CONTENT_BYTES

    def write() -> None:
        try:
            with factory() as session:
                receive_content(session, storage, owner, upload_id, chunks(), clock=clock)
        except BaseException as error:  # conferida pelo teste em `writer.error`
            writer.error = error

    writer.thread = threading.Thread(target=write, daemon=True)
    writer.thread.start()
    try:
        assert writer.first_chunk_sent.wait(timeout=WRITER_TIMEOUT_S)
        yield writer
    finally:
        writer.release.set()
        writer.thread.join(timeout=WRITER_TIMEOUT_S)
    assert not writer.thread.is_alive(), "o escritor não terminou"


def upload_state(factory: sessionmaker[Session], upload_id: uuid.UUID) -> str:
    with factory() as session:
        state: str = session.execute(
            select(upload.c.state).where(upload.c.id == upload_id)
        ).scalar_one()
    return state


def test_upload_recebendo_excluido_mantem_linha_e_reserva_ate_o_escritor_terminar(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=CONTENT_BYTES)

    with blocked_writer(factory, storage, owner, intent.id) as writer:
        with unit_of_work(factory) as session:
            request_deletion(session, owner, intent.id)
        with factory() as session:
            assert expire_incomplete_uploads(session, storage).uploads == 0
        assert (
            live_amount(factory, capacity.HolderKind.UPLOAD, intent.id) == MIN_INTENT_RESERVED_BYTES
        )

    assert isinstance(writer.error, UploadGoneError)


def test_escritor_que_perde_a_vez_apaga_o_arquivo_marca_failed_e_libera_a_reserva(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=CONTENT_BYTES)
    _, storage_key = object_of(factory, intent.id)

    with (
        blocked_writer(factory, storage, owner, intent.id) as writer,
        unit_of_work(factory) as session,
    ):
        request_deletion(session, owner, intent.id)

    assert isinstance(writer.error, UploadGoneError)
    assert upload_state(factory, intent.id) == UploadState.FAILED
    assert live_amount(factory, capacity.HolderKind.UPLOAD, intent.id) is None
    assert account_reserved(factory, owner) == 0
    with pytest.raises(ObjectMissingError):
        storage.stat(storage_key)
    with factory() as session:
        assert expire_incomplete_uploads(session, storage).uploads == 1


def test_upload_recebendo_vencido_sai_pela_retencao_mesmo_com_escritor_preso(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=CONTENT_BYTES)
    _, storage_key = object_of(factory, intent.id)
    clock = FakeClock()

    # Escritor preso (ou morto) esperando o cliente: o arquivo parcial não cresce, e o vencimento
    # é o limite para a reserva dele.
    with blocked_writer(factory, storage, owner, intent.id, clock) as writer:
        expire_row(factory, upload, intent.id)
        clock.advance(INCOMPLETE_UPLOAD_TTL)
        with factory() as session:
            assert expire_incomplete_uploads(session, storage).uploads == 1
        assert live_amount(factory, capacity.HolderKind.UPLOAD, intent.id) is None

    # Ao acordar depois do vencimento, ele não grava o bloco seguinte e apaga o parcial.
    assert isinstance(writer.error, UploadGoneError)
    with pytest.raises(ObjectMissingError):
        storage.stat(storage_key)
    assert list(storage.root.rglob("*.part")) == []


def test_escritor_que_passa_do_vencimento_para_e_so_libera_a_reserva_junto_com_o_arquivo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=CONTENT_BYTES)
    _, storage_key = object_of(factory, intent.id)
    clock = FakeClock()

    with blocked_writer(factory, storage, owner, intent.id, clock) as writer:
        clock.advance(INCOMPLETE_UPLOAD_TTL)
        assert (
            live_amount(factory, capacity.HolderKind.UPLOAD, intent.id) == MIN_INTENT_RESERVED_BYTES
        )

    assert isinstance(writer.error, UploadGoneError)
    assert upload_state(factory, intent.id) == UploadState.FAILED
    assert live_amount(factory, capacity.HolderKind.UPLOAD, intent.id) is None
    assert account_reserved(factory, owner) == 0
    with pytest.raises(ObjectMissingError):
        storage.stat(storage_key)
    assert list(storage.root.rglob("*.part")) == []


def test_falha_ao_apagar_no_escritor_mantem_a_reserva_ate_o_vencimento(
    factory: sessionmaker[Session], storage: FileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=CONTENT_BYTES)
    _, storage_key = object_of(factory, intent.id)
    delete_from_disk = storage.delete

    with blocked_writer(factory, storage, owner, intent.id) as writer:
        with unit_of_work(factory) as session:
            request_deletion(session, owner, intent.id)

        def refuse_in_writer(key: str) -> None:
            if threading.current_thread() is writer.thread:
                raise OSError(errno.EIO, "disco recusou")
            delete_from_disk(key)

        monkeypatch.setattr(storage, "delete", refuse_in_writer)

    assert isinstance(writer.error, OSError)
    assert upload_state(factory, intent.id) == UploadState.RECEIVING
    assert live_amount(factory, capacity.HolderKind.UPLOAD, intent.id) == MIN_INTENT_RESERVED_BYTES
    # O arquivo segue ocupando disco com a reserva; a retenção leva os dois no vencimento.
    expire_row(factory, upload, intent.id)
    with factory() as session:
        assert expire_incomplete_uploads(session, storage).uploads == 1
    assert live_amount(factory, capacity.HolderKind.UPLOAD, intent.id) is None
    with pytest.raises(ObjectMissingError):
        storage.stat(storage_key)


# --- Uploads prontos sem uso ------------------------------------------------------------------


def test_upload_pronto_vencido_sem_uso_e_excluido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    expire_row(factory, upload, view.id)

    with factory() as session:
        assert expire_unused_uploads(session) == 1

    with factory() as session:
        assert get_upload(session, owner, view.id).deleted_at is not None
    assert object_of(factory, view.id)[0] == "delete_requested"


def test_upload_pronto_vencido_em_uso_continua_valido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_in_use(factory, storage, owner, uuid.uuid4())
    expire_row(factory, upload, view.id)

    with factory() as session:
        assert expire_unused_uploads(session) == 0

    assert object_of(factory, view.id)[0] == "committed"


def test_upload_pronto_dentro_do_prazo_nao_expira(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    ready_upload(factory, storage, owner)

    with factory() as session:
        assert expire_unused_uploads(session) == 0


def test_upload_vencido_pela_retencao_aparece_como_expirado_e_nao_como_excluido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    expire_row(factory, upload, view.id)
    with factory() as session:
        expire_unused_uploads(session)

    with factory() as session:
        assert get_upload(session, owner, view.id).availability == Availability.EXPIRED


def test_upload_excluido_pelo_usuario_antes_do_prazo_continua_excluido_depois_dele(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
    pass_expiry_after_deletion(factory, view.id)
    with factory() as session:
        assert expire_unused_uploads(session) == 0

    with factory() as session:
        assert get_upload(session, owner, view.id).availability == Availability.DELETED


# --- Artefatos --------------------------------------------------------------------------------


def test_artefato_vencido_e_excluido_e_objeto_pedido_para_remocao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT, "saida.csv")
        request_deletion(session, owner, view.id)
    assert object_of(factory, view.id)[0] == "committed"
    expire_row(factory, artifact, shared.id)

    with factory() as session:
        assert expire_artifacts(session) == 1

    assert object_of(factory, view.id)[0] == "delete_requested"


def test_artefato_vencido_pela_retencao_aparece_como_expirado_e_nao_como_excluido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT, "saida.csv")
    expire_row(factory, artifact, shared.id)
    with factory() as session:
        expire_artifacts(session)

    with factory() as session:
        assert get_artifact(session, owner, shared.id).availability == Availability.EXPIRED


def test_artefato_excluido_pelo_usuario_antes_do_prazo_continua_excluido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT, "saida.csv")
        session.execute(
            update(artifact).where(artifact.c.id == shared.id).values(deleted_at=func.now())
        )

    with factory() as session:
        assert expire_artifacts(session) == 0
        assert get_artifact(session, owner, shared.id).availability == Availability.DELETED


def test_artefato_vencido_que_divide_objeto_com_upload_valido_preserva_o_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT, "saida.csv")
    expire_row(factory, artifact, shared.id)

    with factory() as session:
        assert expire_artifacts(session) == 1

    assert object_of(factory, view.id)[0] == "committed"


# --- Remoção física ---------------------------------------------------------------------------


def test_exclusao_com_uso_aberto_so_remove_o_arquivo_depois_do_release(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    run_id = uuid.uuid4()
    view = ready_in_use(factory, storage, owner, run_id)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
    _, key = object_of(factory, view.id)

    with factory() as session:
        assert purge_deleted_objects(session, storage).objects == 0
    assert storage.stat(key).size_bytes == 3

    with unit_of_work(factory) as session:
        release(session, HolderKind.RUN, run_id)
    with factory() as session:
        report = purge_deleted_objects(session, storage)

    assert (report.objects, report.bytes, report.limit_reached) == (1, 3, False)
    assert object_of(factory, view.id)[0] == "deleted"
    with pytest.raises(ObjectMissingError):
        storage.stat(key)


def test_falha_ao_apagar_o_arquivo_registra_a_chave_para_o_operador(
    factory: sessionmaker[Session],
    storage: FileStorage,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
    _, key = object_of(factory, view.id)

    def refuse_delete(_key: str) -> None:
        raise PermissionError("disco recusou")

    monkeypatch.setattr(storage, "delete", refuse_delete)
    with factory() as session, caplog.at_level(logging.WARNING, logger=retention.__name__):
        report = purge_deleted_objects(session, storage)

    assert report.failed_keys == (key,)
    records = [r for r in caplog.records if getattr(r, "fields", {}).get("storage_key") == key]
    assert [r.fields["alert"] for r in records] == ["purge_file_not_removed"]  # type: ignore[attr-defined]
    assert records[0].exc_info is not None


def test_download_em_andamento_segura_a_remocao_do_artefato_excluido(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        shared = publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT, "saida.csv")
        request_deletion(session, owner, view.id)
    with unit_of_work(factory) as session:
        ticket = open_for_download(session, owner, shared.id)
    expire_row(factory, artifact, shared.id)
    with factory() as session:
        expire_artifacts(session)

    with factory() as session:
        assert purge_deleted_objects(session, storage).objects == 0
    with storage.open(ticket.storage_key) as stream:
        assert stream.read() == b"abc"


def test_remocao_para_em_500_objetos_por_ciclo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    seed_delete_requested(factory, owner, count=501, size_bytes=1)

    with factory() as session:
        first = purge_deleted_objects(session, storage)
    with factory() as session:
        second = purge_deleted_objects(session, storage)

    assert (first.objects, first.limit_reached) == (500, True)
    assert (second.objects, second.limit_reached) == (1, False)
    assert object_states(factory) == {"deleted": 501}


def test_remocao_para_em_2_gib_por_ciclo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    seed_delete_requested(factory, owner, count=3, size_bytes=GIB)

    with factory() as session:
        report = purge_deleted_objects(session, storage)

    assert (report.objects, report.bytes, report.limit_reached) == (2, 2 * GIB, True)
    assert object_states(factory) == {"deleted": 2, "delete_requested": 1}


def test_objeto_maior_que_o_teto_de_bytes_nao_trava_a_remocao(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    seed_delete_requested(factory, owner, count=2, size_bytes=3 * GIB)

    with factory() as session:
        first = purge_deleted_objects(session, storage)
    with factory() as session:
        second = purge_deleted_objects(session, storage)

    assert (first.objects, first.limit_reached) == (1, True)
    assert (second.objects, second.limit_reached) == (1, False)
    assert object_states(factory) == {"deleted": 2}


def test_remocao_sem_candidatos_nao_faz_nada(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    with factory() as session:
        report = purge_deleted_objects(session, storage)

    assert (report.objects, report.bytes, report.limit_reached) == (0, 0, False)


# --- Próxima liberação de armazenamento -------------------------------------------------------


def expires_in(session: Session, table: Table, row_id: uuid.UUID, wait: timedelta) -> None:
    """Fixa o vencimento na transação da leitura: `now()` é o mesmo e a espera sai exata."""
    session.execute(update(table).where(table.c.id == row_id).values(expires_at=func.now() + wait))


def publish_output(factory: sessionmaker[Session], owner: uuid.UUID, view: UploadView) -> uuid.UUID:
    with unit_of_work(factory) as session:
        [ready] = lock_ready_uploads(session, owner, [view.id])
        return publish_artifact(session, owner, ready.object_id, ArtifactRole.OUTPUT, "s.csv").id


def test_proxima_liberacao_sem_upload_nem_artefato_e_none(
    factory: sessionmaker[Session],
) -> None:
    owner = create_user(factory, "a")

    with unit_of_work(factory) as session:
        assert next_storage_release_in(session, owner) is None


def test_proxima_liberacao_ignora_arquivo_de_outra_conta(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    other = create_user(factory, "b")
    publish_output(factory, other, ready_upload(factory, storage, other))

    with unit_of_work(factory) as session:
        assert next_storage_release_in(session, owner) is None


def test_proxima_liberacao_ignora_upload_e_artefato_excluidos(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    output = publish_output(factory, owner, view)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
        session.execute(
            update(artifact).where(artifact.c.id == output).values(deleted_at=func.now())
        )

    with unit_of_work(factory) as session:
        assert next_storage_release_in(session, owner) is None


def test_proxima_liberacao_e_a_espera_ate_o_upload_vencer(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)

    with unit_of_work(factory) as session:
        expires_in(session, upload, view.id, timedelta(hours=1))
        assert next_storage_release_in(session, owner) == timedelta(hours=1)


def test_proxima_liberacao_escolhe_o_vencimento_mais_proximo_entre_upload_e_artefato(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)
    output = publish_output(factory, owner, view)

    with unit_of_work(factory) as session:
        expires_in(session, upload, view.id, timedelta(hours=2))
        expires_in(session, artifact, output, timedelta(minutes=30))
        assert next_storage_release_in(session, owner) == timedelta(minutes=30)


@pytest.mark.parametrize("overdue", [timedelta(0), timedelta(seconds=1), timedelta(days=3)])
def test_proxima_liberacao_ja_vencida_e_zero_e_nao_negativa(
    factory: sessionmaker[Session], storage: FileStorage, overdue: timedelta
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)

    with unit_of_work(factory) as session:
        expires_in(session, upload, view.id, -overdue)
        assert next_storage_release_in(session, owner) == timedelta(0)
