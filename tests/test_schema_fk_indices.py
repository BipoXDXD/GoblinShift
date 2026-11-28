"""Toda chave estrangeira tem índice na tabela filha, ou um motivo registrado para não ter.

Sem índice, apagar ou atualizar a linha pai varre a tabela filha inteira, e a purga e a retenção
apagam pais em lote. O teste lê o catálogo do banco migrado até `head`, e não o metadata, porque
há índices que só existem nas migrações.

Índice novo só entra com query medida. Por isso as lacunas atuais ficam em `_FK_SEM_INDICE`,
cada uma com o motivo, e o teste trava o resto: FK nova sem índice faz o primeiro teste cair, e
uma lacuna fechada sem tirar a linha da allowlist faz o segundo cair.
"""

from collections.abc import Iterator
from typing import Final

import pytest
from sqlalchemy import Engine, text

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations

type FK = tuple[str, tuple[str, ...]]

# (tabela filha, colunas da FK na ordem da constraint) → por que não há índice. Vazia em 2026-11-28:
# as 7 FKs compostas com `owner_id` estão cobertas por índices das migrações. As duas menos óbvias,
# `artifact(owner_id, source_artifact_id)` e `run(owner_id, preset_id)`, usam índices parciais
# `WHERE <coluna> IS NOT NULL` (`artifact_source_artifact_id_idx`, `run_preset_id_idx`).
_FK_SEM_INDICE: Final[dict[FK, str]] = {}

# Colunas de cada FK, na ordem da constraint.
_FKS = text(
    """
    SELECT c.conrelid::regclass::text AS tabela,
           ARRAY(
               SELECT a.attname::text
               FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordem)
               JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
               ORDER BY k.ordem
           ) AS colunas
    FROM pg_constraint c
    JOIN pg_namespace n ON n.oid = c.connamespace
    WHERE c.contype = 'f' AND n.nspname = 'public'
    """
)

# Colunas-chave de cada índice válido (inclusive PK e unique), na ordem do índice, e o predicado
# do índice parcial. Uma expressão vira '' e não casa com nenhuma coluna; as colunas do INCLUDE
# ficam de fora.
_INDICES = text(
    """
    SELECT i.indrelid::regclass::text AS tabela,
           ARRAY(
               SELECT coalesce(a.attname::text, '')
               FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, ordem)
               LEFT JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
               WHERE k.ordem <= i.indnkeyatts
               ORDER BY k.ordem
           ) AS colunas,
           pg_get_expr(i.indpred, i.indrelid) AS predicado
    FROM pg_index i
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public' AND i.indisvalid
    """
)


def _cobre(indice: tuple[str, ...], fk: tuple[str, ...], predicado: str | None = None) -> bool:
    """O índice serve à busca pela FK.

    Índice parcial só serve com o predicado `(<coluna da FK> IS NOT NULL)`: a busca pela FK
    compara a coluna com um valor, o que já implica `NOT NULL`. Qualquer outro predicado deixa
    linhas de fora. Fora isso, serve quando as primeiras `k` colunas do índice, `k` = nº de
    colunas da FK, são exatamente as colunas da FK, em qualquer ordem. Numa FK (owner_id, x),
    também serve o índice que começa por `x`: o dono sai da própria linha, e `x` sozinho já
    seleciona poucas linhas.
    """
    if predicado is not None and predicado not in {f"({coluna} IS NOT NULL)" for coluna in fk}:
        return False
    if set(indice[: len(fk)]) == set(fk):
        return True
    resto = [coluna for coluna in fk if coluna != "owner_id"]
    return len(resto) == len(fk) - 1 == 1 and indice[:1] == (resto[0],)


def _fks_sem_indice(engine: Engine) -> set[FK]:
    with engine.connect() as conn:
        fks = {(tabela, tuple(colunas)) for tabela, colunas in conn.execute(_FKS)}
        indices: dict[str, list[tuple[tuple[str, ...], str | None]]] = {}
        for tabela, colunas, predicado in conn.execute(_INDICES):
            indices.setdefault(tabela, []).append((tuple(colunas), predicado))
    return {
        (tabela, colunas)
        for tabela, colunas in fks
        if not any(
            _cobre(indice, colunas, predicado) for indice, predicado in indices.get(tabela, [])
        )
    }


@pytest.fixture
def migrated_engine(db_settings: Settings) -> Iterator[Engine]:
    run_migrations(db_settings, "head")
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()


@pytest.mark.integration
def test_toda_fk_tem_indice_ou_motivo_registrado(migrated_engine: Engine) -> None:
    descobertas = _fks_sem_indice(migrated_engine)

    assert descobertas - _FK_SEM_INDICE.keys() == set()


@pytest.mark.integration
def test_allowlist_nao_guarda_fk_que_ja_tem_indice(migrated_engine: Engine) -> None:
    assert _FK_SEM_INDICE.keys() - _fks_sem_indice(migrated_engine) == set()


@pytest.mark.parametrize(
    ("indice", "fk", "predicado", "cobre"),
    [
        (("b", "a"), ("a", "b"), None, True),
        (("owner_id", "x"), ("owner_id", "x"), None, True),
        (("owner_id", "x", "created_at"), ("owner_id", "x"), None, True),
        (("x", "created_at"), ("owner_id", "x"), None, True),
        (("owner_id",), ("owner_id", "x"), None, False),
        (("owner_id", "created_at", "x"), ("owner_id", "x"), None, False),
        (("a", "c"), ("a", "b"), None, False),
        (("", "x"), ("x",), None, False),
        (("x",), ("owner_id", "x"), "(x IS NOT NULL)", True),
        (("x",), ("owner_id", "x"), "(status = 'queued'::text)", False),
        (("x",), ("owner_id", "x"), "(y IS NOT NULL)", False),
    ],
)
def test_regra_de_cobertura_aceita_ordem_trocada_e_recusa_prefixo_parcial(
    indice: tuple[str, ...], fk: tuple[str, ...], predicado: str | None, cobre: bool
) -> None:
    assert _cobre(indice, fk, predicado) is cobre
