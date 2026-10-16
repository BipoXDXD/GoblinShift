"""Construtores de dados dos testes de `files`, sempre pela API pública do módulo."""

import uuid
from datetime import timedelta

from sqlalchemy import func, insert, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import MOUNT_MARKER, FileStorage
from goblinshift.files.tables import upload
from goblinshift.files.uploads import UploadIntent, UploadView, create_intents, receive_content
from goblinshift.identity import CURRENT_TERMS_VERSION
from goblinshift.identity.tables import app_user
from goblinshift.platform.db import unit_of_work


def create_user(factory: sessionmaker[Session], subject: str) -> uuid.UUID:
    with unit_of_work(factory) as session:
        user_id: uuid.UUID = session.execute(
            insert(app_user)
            .values(
                issuer="test",
                subject=subject,
                display_name=subject,
                # Conta comum já ativa: aceitou os termos vigentes (ADR 029).
                terms_version=CURRENT_TERMS_VERSION,
                terms_accepted_at=func.now(),
            )
            .returning(app_user.c.id)
        ).scalar_one()
    return user_id


def create_intent(
    factory: sessionmaker[Session], owner_id: uuid.UUID, name: str = "a.txt", size: int = 3
) -> UploadView:
    with unit_of_work(factory) as session:
        [view] = create_intents(session, owner_id, [UploadIntent(name, size)])
    return view


def ready_upload(
    factory: sessionmaker[Session],
    storage: FileStorage,
    owner_id: uuid.UUID,
    content: bytes = b"abc",
) -> UploadView:
    intent = create_intent(factory, owner_id, size=len(content))
    with factory() as session:
        return receive_content(session, storage, owner_id, intent.id, [content])


def pass_expiry_after_deletion(factory: sessionmaker[Session], upload_id: uuid.UUID) -> None:
    """Simula o tempo passando: o dono excluiu há 2 h e o prazo de retenção venceu há 1 h."""
    with unit_of_work(factory) as session:
        session.execute(
            update(upload)
            .where(upload.c.id == upload_id)
            .values(
                deleted_at=func.now() - timedelta(hours=2),
                expires_at=func.now() - timedelta(hours=1),
            )
        )


def mark_mounted(storage: FileStorage) -> None:
    """A sentinela que o `storage-mark` grava no filesystem montado (deploy/storage-mark.sh)."""
    storage.root.mkdir(parents=True, exist_ok=True)
    (storage.root / MOUNT_MARKER).touch()
