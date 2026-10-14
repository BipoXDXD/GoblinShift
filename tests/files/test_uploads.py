"""Intenções, recebimento de conteúdo, consulta e exclusão de uploads (1.6, 1.7 e 3.6)."""

import hashlib
import threading
import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.errors import (
    InvalidUploadRequestError,
    UploadContentTooLargeError,
    UploadGoneError,
    UploadNotFoundError,
    UploadSizeMismatchError,
    UploadStateConflictError,
)
from goblinshift.files.states import Availability, UploadState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object, upload
from goblinshift.files.uploads import (
    UploadIntent,
    create_intents,
    get_upload,
    receive_content,
    request_deletion,
)
from goblinshift.platform.db import unit_of_work
from tests.files.support import create_intent, create_user, ready_upload

pytestmark = pytest.mark.integration

MIB = 1024 * 1024
MAX_FILE_BYTES = 32 * MIB
MAX_BATCH_BYTES = 512 * MIB


class ClientDisconnectedError(Exception):
    pass


def count_rows(factory: sessionmaker[Session]) -> tuple[int, int]:
    with factory() as session:
        uploads = session.execute(select(func.count()).select_from(upload)).scalar_one()
        objects = session.execute(select(func.count()).select_from(stored_object)).scalar_one()
    return uploads, objects


def object_state(factory: sessionmaker[Session], upload_id: uuid.UUID) -> str:
    with factory() as session:
        state: str = session.execute(
            select(stored_object.c.state)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == upload_id)
        ).scalar_one()
    return state


def files_under(storage: FileStorage) -> list[str]:
    return [p.name for p in storage.root.rglob("*") if p.is_file()]


# --- Intenções --------------------------------------------------------------------------------


def test_lote_cria_intencoes_na_ordem_enviada(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")

    with unit_of_work(factory) as session:
        views = create_intents(session, owner, [("b.csv", 10), ("a.png", 0)])

    assert [(v.original_name, v.expected_size) for v in views] == [("b.csv", 10), ("a.png", 0)]
    assert all(v.state is UploadState.CREATED for v in views)
    assert all(v.sha256 is None and v.size_bytes is None for v in views)
    assert len({v.id for v in views}) == 2


def test_intencao_expira_uma_hora_depois_da_criacao(factory: sessionmaker[Session]) -> None:
    view = create_intent(factory, create_user(factory, "a"))

    assert view.expires_at - view.created_at == timedelta(hours=1)


def test_lote_com_100_itens_e_aceito(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")

    with unit_of_work(factory) as session:
        views = create_intents(session, owner, [(f"f{i}.txt", 1) for i in range(100)])

    assert len(views) == 100


def test_lote_com_101_itens_e_recusado_sem_gravar_nada(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")

    with pytest.raises(InvalidUploadRequestError) as error, unit_of_work(factory) as session:
        create_intents(session, owner, [(f"f{i}.txt", 1) for i in range(101)])

    assert error.value.field == "items"
    assert count_rows(factory) == (0, 0)


def test_lote_vazio_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")

    with pytest.raises(InvalidUploadRequestError) as error, unit_of_work(factory) as session:
        create_intents(session, owner, [])

    assert error.value.field == "items"


def test_soma_de_exatamente_512_mib_e_aceita(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    items = [(f"f{i}", MAX_FILE_BYTES) for i in range(MAX_BATCH_BYTES // MAX_FILE_BYTES)]

    with unit_of_work(factory) as session:
        views = create_intents(session, owner, items)

    assert sum(v.expected_size for v in views) == MAX_BATCH_BYTES


def test_soma_um_byte_acima_de_512_mib_e_recusada(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    items = [(f"f{i}", MAX_FILE_BYTES) for i in range(MAX_BATCH_BYTES // MAX_FILE_BYTES)]
    items.append(("extra", 1))

    with pytest.raises(InvalidUploadRequestError) as error, unit_of_work(factory) as session:
        create_intents(session, owner, items)

    assert error.value.field == "items"
    assert count_rows(factory) == (0, 0)


@pytest.mark.parametrize("size", [0, MAX_FILE_BYTES])
def test_tamanho_nas_bordas_e_aceito(factory: sessionmaker[Session], size: int) -> None:
    view = create_intent(factory, create_user(factory, "a"), size=size)

    assert view.expected_size == size


@pytest.mark.parametrize("size", [-1, MAX_FILE_BYTES + 1])
def test_tamanho_fora_das_bordas_e_recusado(factory: sessionmaker[Session], size: int) -> None:
    owner = create_user(factory, "a")

    with pytest.raises(InvalidUploadRequestError) as error, unit_of_work(factory) as session:
        create_intents(session, owner, [("ok.txt", 1), ("a.txt", size)])

    assert error.value.field == "items[1].expected_size"


@pytest.mark.parametrize("name", ["a", "x" * 255, "relatório final (1).CSV", "..oculto", "a.b."])
def test_nome_valido_e_guardado_como_recebido(factory: sessionmaker[Session], name: str) -> None:
    view = create_intent(factory, create_user(factory, "a"), name=name)

    assert view.original_name == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "x" * 256,
        ".",
        "..",
        "a/b",
        "../etc/passwd",
        "a\\b",
        "a\x00b",
        "a\nb",
        "a\x7fb",
        "a\u2028b",
        "\ud800",
    ],
)
def test_nome_invalido_e_recusado(factory: sessionmaker[Session], name: str) -> None:
    owner = create_user(factory, "a")

    with pytest.raises(InvalidUploadRequestError) as error, unit_of_work(factory) as session:
        create_intents(session, owner, [UploadIntent(name, 1)])

    assert error.value.field == "items[0].name"
    assert count_rows(factory) == (0, 0)


# --- Recebimento de conteúdo ------------------------------------------------------------------


def test_conteudo_completo_deixa_upload_pronto_com_hash_e_tamanho(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=6)

    with factory() as session:
        view = receive_content(session, storage, owner, intent.id, [b"abc", b"def"])

    assert view.state is UploadState.READY
    assert view.size_bytes == 6
    assert view.sha256 == hashlib.sha256(b"abcdef").digest()
    assert view.ready_at is not None
    assert view.expires_at - view.ready_at == timedelta(hours=24)
    assert object_state(factory, intent.id) == "committed"
    assert files_under(storage) != []


def test_conteudo_de_32_mib_exatos_e_aceito(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=MAX_FILE_BYTES)
    chunks = (b"\x00" * MIB for _ in range(32))

    with factory() as session:
        view = receive_content(session, storage, owner, intent.id, chunks)

    assert view.size_bytes == MAX_FILE_BYTES


@pytest.mark.parametrize("content", [b"ab", b"abcd"])
def test_tamanho_real_diferente_do_declarado_falha_e_apaga_o_objeto(
    factory: sessionmaker[Session], storage: FileStorage, content: bytes
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=3)

    with pytest.raises(UploadSizeMismatchError), factory() as session:
        receive_content(session, storage, owner, intent.id, [content])

    with factory() as session:
        assert get_upload(session, owner, intent.id).state is UploadState.FAILED
    assert object_state(factory, intent.id) == "pending"
    assert files_under(storage) == []


def test_conteudo_maior_que_o_declarado_tem_erro_proprio(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=3)

    with pytest.raises(UploadContentTooLargeError) as error, factory() as session:
        receive_content(session, storage, owner, intent.id, [b"ab", b"cd"])

    assert error.value.expected_size == 3
    with factory() as session:
        assert get_upload(session, owner, intent.id).state is UploadState.FAILED


def test_conteudo_menor_que_o_declarado_nao_e_erro_de_excesso(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=3)

    with pytest.raises(UploadSizeMismatchError) as error, factory() as session:
        receive_content(session, storage, owner, intent.id, [b"ab"])

    assert not isinstance(error.value, UploadContentTooLargeError)


def test_envio_interrompido_falha_sem_deixar_arquivo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=3)

    def interrupted() -> Iterator[bytes]:
        yield b"a"
        raise ClientDisconnectedError

    with pytest.raises(ClientDisconnectedError), factory() as session:
        receive_content(session, storage, owner, intent.id, interrupted())

    with factory() as session:
        assert get_upload(session, owner, intent.id).state is UploadState.FAILED
    assert files_under(storage) == []


def test_upload_falho_nao_reabre_para_novo_envio(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=3)
    with pytest.raises(UploadSizeMismatchError), factory() as session:
        receive_content(session, storage, owner, intent.id, [b"x"])

    with pytest.raises(UploadStateConflictError) as error, factory() as session:
        receive_content(session, storage, owner, intent.id, [b"abc"])

    assert error.value.state is UploadState.FAILED


def test_upload_pronto_nao_aceita_segundo_envio(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner, b"abc")

    with pytest.raises(UploadStateConflictError), factory() as session:
        receive_content(session, storage, owner, view.id, [b"xyz"])

    with storage.open(_storage_key(factory, view.id)) as stream:
        assert stream.read() == b"abc"


def test_dois_escritores_concorrentes_so_um_passa(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=6)
    first_chunk_sent = threading.Event()
    second_writer_done = threading.Event()
    outcome: dict[str, object] = {}

    def slow_chunks() -> Iterator[bytes]:
        yield b"abc"
        first_chunk_sent.set()
        # O primeiro escritor só termina depois que o segundo tentou entrar.
        second_writer_done.wait(timeout=10)
        yield b"def"

    def first_writer() -> None:
        with factory() as session:
            outcome["first"] = receive_content(session, storage, owner, intent.id, slow_chunks())

    thread = threading.Thread(target=first_writer)
    thread.start()
    assert first_chunk_sent.wait(timeout=10)
    try:
        with pytest.raises(UploadStateConflictError) as error, factory() as session:
            receive_content(session, storage, owner, intent.id, [b"zzzzzz"])
    finally:
        second_writer_done.set()
        thread.join(timeout=10)

    assert error.value.state is UploadState.RECEIVING
    with factory() as session:
        final = get_upload(session, owner, intent.id)
    assert final.state is UploadState.READY
    assert final.sha256 == hashlib.sha256(b"abcdef").digest()


def test_upload_expirado_nao_aceita_conteudo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=3)
    _expire_now(factory, intent.id)

    with pytest.raises(UploadGoneError), factory() as session:
        receive_content(session, storage, owner, intent.id, [b"abc"])


def test_upload_excluido_nao_aceita_conteudo(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner, size=3)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, intent.id)

    with pytest.raises(UploadGoneError), factory() as session:
        receive_content(session, storage, owner, intent.id, [b"abc"])
    assert files_under(storage) == []


def test_upload_novo_esta_disponivel(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner)

    with factory() as session:
        view = get_upload(session, owner, intent.id)

    assert intent.availability is Availability.AVAILABLE
    assert view.availability is Availability.AVAILABLE


def test_upload_vencido_aparece_como_expirado(factory: sessionmaker[Session]) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner)
    _expire_now(factory, intent.id)

    with factory() as session:
        assert get_upload(session, owner, intent.id).availability is Availability.EXPIRED


def test_upload_excluido_aparece_como_excluido_mesmo_vencido(
    factory: sessionmaker[Session],
) -> None:
    owner = create_user(factory, "a")
    intent = create_intent(factory, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, intent.id)
    _expire_now(factory, intent.id)

    with factory() as session:
        assert get_upload(session, owner, intent.id).availability is Availability.DELETED


# --- Isolamento entre contas ------------------------------------------------------------------


def test_dono_b_recebe_o_mesmo_erro_de_upload_inexistente(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner_a = create_user(factory, "a")
    owner_b = create_user(factory, "b")
    upload_a = ready_upload(factory, storage, owner_a, b"segredo de A")
    missing = uuid.uuid4()

    errors = []
    for upload_id in [upload_a.id, missing]:
        with pytest.raises(UploadNotFoundError) as read_error, factory() as session:
            get_upload(session, owner_b, upload_id)
        with pytest.raises(UploadNotFoundError) as write_error, factory() as session:
            receive_content(session, storage, owner_b, upload_id, [b"x"])
        with pytest.raises(UploadNotFoundError) as delete_error, unit_of_work(factory) as session:
            request_deletion(session, owner_b, upload_id)
        errors.append(
            [
                str(e.value).replace(str(upload_id), "<id>")
                for e in (read_error, write_error, delete_error)
            ]
        )

    assert errors[0] == errors[1]
    with factory() as session:
        intact = get_upload(session, owner_a, upload_a.id)
    assert intact == upload_a


# --- Exclusão ---------------------------------------------------------------------------------


def test_exclusao_marca_upload_e_pede_remocao_do_objeto(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)

    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)

    with factory() as session:
        deleted = get_upload(session, owner, view.id)
    assert deleted.deleted_at is not None
    assert object_state(factory, view.id) == "delete_requested"


def test_exclusao_repetida_e_sucesso(factory: sessionmaker[Session], storage: FileStorage) -> None:
    owner = create_user(factory, "a")
    view = ready_upload(factory, storage, owner)

    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)
    with factory() as session:
        first = get_upload(session, owner, view.id).deleted_at
    with unit_of_work(factory) as session:
        request_deletion(session, owner, view.id)

    with factory() as session:
        assert get_upload(session, owner, view.id).deleted_at == first


def _storage_key(factory: sessionmaker[Session], upload_id: uuid.UUID) -> str:
    with factory() as session:
        key: str = session.execute(
            select(stored_object.c.storage_key)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == upload_id)
        ).scalar_one()
    return key


def _expire_now(factory: sessionmaker[Session], upload_id: uuid.UUID) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == upload_id)
            .values(expires_at=func.now() - timedelta(seconds=1))
        )
