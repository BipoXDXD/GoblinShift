"""Presets da conta: criar, ler, substituir e excluir com controle de revisão (1.6 e 1.7).

Supporting, em transaction script sobre a Session de quem chama. Toda consulta filtra por
`owner_id` na própria query: preset de outra conta é indistinguível de inexistente.

Escritas que mudam nome ou quantidade (criar e substituir) serializam por conta com um advisory
lock de transação: "contar e depois inserir" e "nome livre e depois gravar" seriam write skew sob
READ COMMITTED. A seção crítica lê poucas linhas de uma conta só, então a espera é curta.
Substituir e excluir só acontecem na revisão lida (`expected_revision`): `UPDATE`/`DELETE`
condicional, sem ler antes para decidir.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Select, delete, exists, func, insert, select, update
from sqlalchemy.orm import Session

from goblinshift.presets.drafts import PresetDraft
from goblinshift.presets.tables import preset

MAX_PRESETS_PER_ACCOUNT = 100
_LOCK_NAMESPACE = "goblinshift.presets.owner"
_COLUMNS = (
    preset.c.id,
    preset.c.name,
    preset.c.recipe_id,
    preset.c.recipe_version,
    preset.c.parameters,
    preset.c.revision,
    preset.c.created_at,
    preset.c.updated_at,
)


class PresetNotFoundError(LookupError):
    """Inexistente ou de outra conta (→ 404)."""


class PresetLimitReachedError(Exception):
    """A conta já tem `MAX_PRESETS_PER_ACCOUNT` presets (→ 409)."""


class PresetNameTakenError(Exception):
    """Outro preset da conta já usa o nome (→ 409)."""


class StaleRevisionError(Exception):
    """O preset mudou depois da leitura de quem pede a escrita (→ 412)."""


@dataclass(frozen=True)
class Preset:
    id: UUID
    name: str
    recipe_id: str
    recipe_version: int
    parameters: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime


def create_preset(session: Session, owner_id: UUID, draft: PresetDraft) -> Preset:
    _lock_owner(session, owner_id)
    count: int = session.execute(
        select(func.count()).select_from(preset).where(preset.c.owner_id == owner_id)
    ).scalar_one()
    if count >= MAX_PRESETS_PER_ACCOUNT:
        raise PresetLimitReachedError
    _require_free_name(session, owner_id, draft.name, keep=None)
    row = session.execute(
        insert(preset).values(owner_id=owner_id, **_draft_values(draft)).returning(*_COLUMNS)
    ).one()
    return _preset(row)


def list_presets(session: Session, owner_id: UUID) -> list[Preset]:
    """Todos os da conta (no máximo 100), em ordem de nome."""
    rows = session.execute(
        _select_presets().where(preset.c.owner_id == owner_id).order_by(preset.c.name, preset.c.id)
    ).all()
    return [_preset(row) for row in rows]


def get_preset(session: Session, owner_id: UUID, preset_id: UUID) -> Preset:
    row = session.execute(
        _select_presets().where(preset.c.owner_id == owner_id, preset.c.id == preset_id)
    ).one_or_none()
    if row is None:
        raise PresetNotFoundError
    return _preset(row)


def hold_for_run(session: Session, owner_id: UUID, preset_id: UUID) -> None:
    """Trava o preset contra exclusão até o fim da transação de quem chama (`FOR KEY SHARE`).

    Editar o preset continua possível (o `UPDATE` só de colunas que não são chave não espera por
    essa trava); excluir espera o commit, e então a FK da Run vira nula.
    """
    found = session.execute(
        select(preset.c.id)
        .where(preset.c.owner_id == owner_id, preset.c.id == preset_id)
        .with_for_update(read=True, key_share=True)
    ).one_or_none()
    if found is None:
        raise PresetNotFoundError


def replace_preset(
    session: Session, owner_id: UUID, preset_id: UUID, expected_revision: int, draft: PresetDraft
) -> Preset:
    """Atualização integral na revisão lida; a revisão sobe 1."""
    _lock_owner(session, owner_id)
    # A revisão antes do nome: quem leu uma versão velha recebe 412 e relê, em vez de um 409 que
    # talvez nem valha para a versão atual.
    _require_revision(session, owner_id, preset_id, expected_revision)
    _require_free_name(session, owner_id, draft.name, keep=preset_id)
    row = session.execute(
        update(preset)
        .where(
            preset.c.owner_id == owner_id,
            preset.c.id == preset_id,
            preset.c.revision == expected_revision,
        )
        .values(**_draft_values(draft), revision=preset.c.revision + 1, updated_at=func.now())
        .returning(*_COLUMNS)
    ).one_or_none()
    if row is None:
        raise _missed_write(session, owner_id, preset_id)
    return _preset(row)


def delete_preset(
    session: Session, owner_id: UUID, preset_id: UUID, expected_revision: int
) -> None:
    """Remove na revisão lida; as execuções que o usaram ficam com `preset_id` nulo."""
    deleted = session.execute(
        delete(preset)
        .where(
            preset.c.owner_id == owner_id,
            preset.c.id == preset_id,
            preset.c.revision == expected_revision,
        )
        .returning(preset.c.id)
    ).one_or_none()
    if deleted is None:
        raise _missed_write(session, owner_id, preset_id)


def _select_presets() -> Select[Any]:
    return select(*_COLUMNS)


def _lock_owner(session: Session, owner_id: UUID) -> None:
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"{_LOCK_NAMESPACE}.{owner_id}")))
    )


def _require_revision(
    session: Session, owner_id: UUID, preset_id: UUID, expected_revision: int
) -> None:
    current = session.execute(
        select(preset.c.revision).where(preset.c.owner_id == owner_id, preset.c.id == preset_id)
    ).scalar_one_or_none()
    if current is None:
        raise PresetNotFoundError
    if current != expected_revision:
        raise StaleRevisionError


def _require_free_name(session: Session, owner_id: UUID, name: str, keep: UUID | None) -> None:
    others = [preset.c.owner_id == owner_id, preset.c.name == name]
    if keep is not None:
        others.append(preset.c.id != keep)
    if session.execute(select(exists().where(*others))).scalar_one():
        raise PresetNameTakenError


def _missed_write(session: Session, owner_id: UUID, preset_id: UUID) -> Exception:
    """A escrita condicional não achou a linha: revisão vencida ou preset ausente para a conta."""
    found = session.execute(
        select(exists().where(preset.c.owner_id == owner_id, preset.c.id == preset_id))
    ).scalar_one()
    return StaleRevisionError() if found else PresetNotFoundError()


def _draft_values(draft: PresetDraft) -> dict[str, object]:
    return {
        "name": draft.name,
        "recipe_id": draft.recipe.recipe_id,
        "recipe_version": draft.recipe.version,
        "parameters": draft.parameters,
    }


def _preset(row: Row[Any]) -> Preset:
    return Preset(
        id=row.id,
        name=row.name,
        recipe_id=row.recipe_id,
        recipe_version=row.recipe_version,
        parameters=row.parameters,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
