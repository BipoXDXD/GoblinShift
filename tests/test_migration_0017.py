"""Migração 0017: o `TERMS_ACCEPTED` pode registrar que o aceite veio do `gs-admin` (ADR 029).

Expand: o CHECK `audit_event_details_check` passa a aceitar, no `TERMS_ACCEPTED`, o detalhe fixo
`{"channel": "admin_cli"}`, além do NULL do aceite pela sessão web. Qualquer outro detalhe nesse
tipo continua recusado, para nenhum dado pessoal entrar por ali. O downgrade anula esse detalhe,
que o CHECK antigo recusaria, e mantém os eventos.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.conftest import downgrade_migrations, run_migrations

pytestmark = pytest.mark.integration

ADMIN_CLI = '{"channel": "admin_cli"}'
CHANGES = '{"changes": {"runs_per_24h": {"old": null, "new": 5}}}'


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    engine = make_engine(db_settings)
    yield engine
    engine.dispose()
    run_migrations(db_settings, "head")


def record(engine: Engine, event_type: str, details: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_event (event_type, outcome, details) "
                "VALUES (:event_type, 'success', CAST(:details AS jsonb))"
            ),
            {"event_type": event_type, "details": details},
        )


def count(engine: Engine, event_type: str, *, with_details: bool) -> int:
    with engine.connect() as conn:
        found: int = conn.execute(
            text(
                "SELECT count(*) FROM audit_event "
                "WHERE event_type = :event_type AND (details IS NOT NULL) = :with_details"
            ),
            {"event_type": event_type, "with_details": with_details},
        ).scalar_one()
    return found


@pytest.mark.parametrize("details", [None, ADMIN_CLI], ids=["web", "admin-cli"])
def test_0017_aceita_o_aceite_web_e_o_do_gs_admin(
    db_settings: Settings, engine: Engine, details: str | None
) -> None:
    run_migrations(db_settings, "0017")
    with_details = details is not None
    before = count(engine, "TERMS_ACCEPTED", with_details=with_details)

    record(engine, "TERMS_ACCEPTED", details)

    assert count(engine, "TERMS_ACCEPTED", with_details=with_details) == before + 1


@pytest.mark.parametrize(
    ("event_type", "details"),
    [
        ("TERMS_ACCEPTED", '{"channel": "web"}'),
        ("TERMS_ACCEPTED", '{"channel": "admin_cli", "email": "ana@example.com"}'),
        ("TERMS_ACCEPTED", CHANGES),
        ("TERMS_ACCEPTED", '"admin_cli"'),
        ("ACCOUNT_BLOCKED", ADMIN_CLI),
        ("ACCOUNT_LIMITS_CHANGED", None),
    ],
    ids=[
        "outro-canal",
        "campo-extra",
        "detalhe-de-limites",
        "texto-solto",
        "canal-em-outro-tipo",
        "limites-sem-detalhe",
    ],
)
def test_detalhe_do_aceite_e_so_o_canal_fixo(
    db_settings: Settings, engine: Engine, event_type: str, details: str | None
) -> None:
    run_migrations(db_settings, "0017")

    with pytest.raises(IntegrityError, match="audit_event_details_check"):
        record(engine, event_type, details)


def test_mudanca_de_limites_continua_com_detalhe(db_settings: Settings, engine: Engine) -> None:
    run_migrations(db_settings, "0017")
    before = count(engine, "ACCOUNT_LIMITS_CHANGED", with_details=True)

    record(engine, "ACCOUNT_LIMITS_CHANGED", CHANGES)

    assert count(engine, "ACCOUNT_LIMITS_CHANGED", with_details=True) == before + 1


def test_downgrade_anula_o_canal_mantem_os_eventos_e_restaura_o_check(
    db_settings: Settings, engine: Engine
) -> None:
    run_migrations(db_settings, "0017")
    record(engine, "TERMS_ACCEPTED", ADMIN_CLI)
    # O banco não é limpo entre estes testes: conta o que já existe.
    accepted = count(engine, "TERMS_ACCEPTED", with_details=False) + count(
        engine, "TERMS_ACCEPTED", with_details=True
    )

    downgrade_migrations(db_settings, "0016")

    assert count(engine, "TERMS_ACCEPTED", with_details=True) == 0
    assert count(engine, "TERMS_ACCEPTED", with_details=False) == accepted
    with pytest.raises(IntegrityError, match="audit_event_details_check"):
        record(engine, "TERMS_ACCEPTED", ADMIN_CLI)
