"""Presets privados com revisão, com PostgreSQL real (1.6, 1.7 e 3.3)."""

import threading
from collections.abc import Callable
from functools import partial
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.platform.db import unit_of_work
from goblinshift.presets import (
    MAX_PRESETS_PER_ACCOUNT,
    Preset,
    PresetDraft,
    PresetLimitReachedError,
    PresetNameTakenError,
    PresetNotFoundError,
    StaleRevisionError,
    create_preset,
    delete_preset,
    get_preset,
    list_presets,
    parse_draft,
    replace_preset,
)
from goblinshift.presets.tables import preset
from tests.identity.support import new_user

pytestmark = pytest.mark.integration


def draft(name: str = "Limpeza", **parameters: object) -> PresetDraft:
    return parse_draft(name, "csv-janitor", 1, parameters)


def create(factory: sessionmaker[Session], owner: UUID, name: str = "Limpeza") -> Preset:
    with unit_of_work(factory) as session:
        return create_preset(session, owner, draft(name))


def in_session[T](factory: sessionmaker[Session], action: Callable[[Session], T]) -> T:
    with unit_of_work(factory) as session:
        return action(session)


def count_presets(factory: sessionmaker[Session], owner: UUID) -> int:
    with factory() as session:
        total: int = session.execute(
            select(func.count()).select_from(preset).where(preset.c.owner_id == owner)
        ).scalar_one()
    return total


# Criar


def test_criar_devolve_o_preset_na_revisao_1(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)

    created = in_session(
        factory, lambda s: create_preset(s, owner, draft("Mensal", trim_columns=["a"]))
    )

    assert created.name == "Mensal"
    assert created.recipe_id == "csv-janitor"
    assert created.recipe_version == 1
    assert created.parameters["trim_columns"] == ["a"]
    assert created.revision == 1
    assert created.created_at == created.updated_at


def test_nome_repetido_na_mesma_conta_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    create(factory, owner, "Mensal")

    with pytest.raises(PresetNameTakenError):
        create(factory, owner, "Mensal")

    assert count_presets(factory, owner) == 1


def test_contas_diferentes_podem_usar_o_mesmo_nome(factory: sessionmaker[Session]) -> None:
    create(factory, new_user(factory), "Mensal")

    created = create(factory, new_user(factory), "Mensal")

    assert created.name == "Mensal"


def test_ate_100_presets_por_conta(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    for index in range(MAX_PRESETS_PER_ACCOUNT - 1):
        create(factory, owner, f"p{index}")

    create(factory, owner, "centesimo")
    with pytest.raises(PresetLimitReachedError):
        create(factory, owner, "excedente")

    assert MAX_PRESETS_PER_ACCOUNT == 100
    assert count_presets(factory, owner) == 100


def test_limite_vale_com_criacoes_simultaneas(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    for index in range(MAX_PRESETS_PER_ACCOUNT - 1):
        create(factory, owner, f"p{index}")
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def attempt(name: str) -> None:
        barrier.wait()
        try:
            create(factory, owner, name)
            outcomes.append("criado")
        except PresetLimitReachedError:
            outcomes.append("recusado")

    threads = [threading.Thread(target=attempt, args=(f"corrida{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["criado", "recusado", "recusado", "recusado"]
    assert count_presets(factory, owner) == MAX_PRESETS_PER_ACCOUNT


# Ler


def test_listagem_traz_so_os_da_conta_em_ordem_de_nome(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    create(factory, owner, "b")
    create(factory, owner, "a")
    create(factory, new_user(factory), "alheio")

    listed = in_session(factory, lambda s: list_presets(s, owner))

    assert [item.name for item in listed] == ["a", "b"]


def test_listagem_vazia(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)

    assert in_session(factory, lambda s: list_presets(s, owner)) == []


def test_ler_preset_de_outra_conta_e_igual_a_inexistente(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    other = create(factory, new_user(factory))

    for preset_id in (other.id, uuid4()):
        with pytest.raises(PresetNotFoundError):
            in_session(factory, partial(get_preset, owner_id=owner, preset_id=preset_id))


# Substituir


def test_substituir_com_a_revisao_lida_troca_tudo_e_sobe_a_revisao(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    created = create(factory, owner, "Antigo")
    new_draft = parse_draft("Novo", "image-forge", 1, {"make_thumbnails": True})

    replaced = in_session(factory, lambda s: replace_preset(s, owner, created.id, 1, new_draft))

    assert replaced.id == created.id
    assert replaced.name == "Novo"
    assert replaced.recipe_id == "image-forge"
    assert replaced.parameters["make_thumbnails"] is True
    assert replaced.revision == 2
    assert replaced.created_at == created.created_at
    assert replaced.updated_at >= created.updated_at
    assert in_session(factory, lambda s: get_preset(s, owner, created.id)) == replaced


def test_substituir_com_revisao_desatualizada_nao_muda_nada(
    factory: sessionmaker[Session],
) -> None:
    owner = new_user(factory)
    created = create(factory, owner, "Antigo")
    in_session(factory, lambda s: replace_preset(s, owner, created.id, 1, draft("Segundo")))

    with pytest.raises(StaleRevisionError):
        in_session(factory, lambda s: replace_preset(s, owner, created.id, 1, draft("Terceiro")))

    current = in_session(factory, lambda s: get_preset(s, owner, created.id))
    assert (current.name, current.revision) == ("Segundo", 2)


def test_substituir_preset_de_outra_conta_e_igual_a_inexistente(
    factory: sessionmaker[Session],
) -> None:
    victim = new_user(factory)
    created = create(factory, victim, "Da vítima")
    attacker = new_user(factory)

    with pytest.raises(PresetNotFoundError):
        in_session(factory, lambda s: replace_preset(s, attacker, created.id, 1, draft("x")))

    assert in_session(factory, lambda s: get_preset(s, victim, created.id)) == created


def test_renomear_para_nome_de_outro_preset_e_recusado(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    create(factory, owner, "Ocupado")
    created = create(factory, owner, "Livre")

    with pytest.raises(PresetNameTakenError):
        in_session(factory, lambda s: replace_preset(s, owner, created.id, 1, draft("Ocupado")))


def test_manter_o_proprio_nome_ao_substituir_e_aceito(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    created = create(factory, owner, "Mesmo")

    replaced = in_session(
        factory,
        lambda s: replace_preset(s, owner, created.id, 1, draft("Mesmo", trim_columns=["b"])),
    )

    assert replaced.revision == 2


# Excluir


def test_excluir_com_a_revisao_lida_remove(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    created = create(factory, owner)

    in_session(factory, lambda s: delete_preset(s, owner, created.id, 1))

    assert count_presets(factory, owner) == 0


def test_excluir_com_revisao_desatualizada_nao_remove(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    created = create(factory, owner)
    in_session(factory, lambda s: replace_preset(s, owner, created.id, 1, draft("Outro")))

    with pytest.raises(StaleRevisionError):
        in_session(factory, lambda s: delete_preset(s, owner, created.id, 1))

    assert count_presets(factory, owner) == 1


def test_excluir_preset_de_outra_conta_e_igual_a_inexistente(
    factory: sessionmaker[Session],
) -> None:
    victim = new_user(factory)
    created = create(factory, victim)
    attacker = new_user(factory)

    for preset_id in (created.id, uuid4()):
        with pytest.raises(PresetNotFoundError):
            in_session(
                factory,
                partial(delete_preset, owner_id=attacker, preset_id=preset_id, expected_revision=1),
            )

    assert count_presets(factory, victim) == 1


def test_excluir_de_novo_e_inexistente(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    created = create(factory, owner)
    in_session(factory, lambda s: delete_preset(s, owner, created.id, 1))

    with pytest.raises(PresetNotFoundError):
        in_session(factory, lambda s: delete_preset(s, owner, created.id, 1))


def test_excluir_libera_vaga_no_limite(factory: sessionmaker[Session]) -> None:
    owner = new_user(factory)
    first = create(factory, owner, "p0")
    for index in range(1, MAX_PRESETS_PER_ACCOUNT):
        create(factory, owner, f"p{index}")
    in_session(factory, lambda s: delete_preset(s, owner, first.id, 1))

    assert create(factory, owner, "nova vaga").revision == 1
