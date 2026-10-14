"""Construtores de dados dos testes de `files`, sempre pela API pública do módulo."""

import uuid

from sqlalchemy import insert
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.files.uploads import UploadIntent, UploadView, create_intents, receive_content
from goblinshift.identity.tables import app_user
from goblinshift.platform.db import unit_of_work


def create_user(factory: sessionmaker[Session], subject: str) -> uuid.UUID:
    with unit_of_work(factory) as session:
        user_id: uuid.UUID = session.execute(
            insert(app_user)
            .values(issuer="test", subject=subject, display_name=subject)
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
