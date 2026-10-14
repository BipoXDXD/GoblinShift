"""Peças puras da telemetria (docs/GoblinShiftPlan.md, 3.7): hash, IP truncado e erro do banco."""

from uuid import UUID

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode
from pydantic import SecretStr, ValidationError

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import MissingSecretError, Settings
from goblinshift.platform.telemetry import (
    UserHasher,
    make_span_processor,
    truncate_client_address,
)

OWNER = UUID("00000000-0000-0000-0000-000000000001")


def test_user_hash_e_o_hmac_sha256_do_owner_id_com_o_segredo() -> None:
    # Calculado à mão: hmac.new(b"k" * 32, OWNER.bytes, sha256).hexdigest()
    expected = "f10a472de832bb7dc5d2658295bfd945af2a2046c8e59f13c0af4a2cc8797800"

    assert UserHasher(b"k" * 32).hash(OWNER) == expected


def test_user_hash_muda_com_o_segredo() -> None:
    assert UserHasher(b"j" * 32).hash(OWNER) == (
        "c53dbfb8bee19c65767a352ea6b36550f153d30835616ea12082f103ab03bf73"
    )


def test_user_hash_nao_contem_o_owner_id() -> None:
    digest = UserHasher(b"k" * 32).hash(OWNER)

    assert str(OWNER) not in digest
    assert OWNER.hex not in digest


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("203.0.113.77", "203.0.113.0"),
        ("203.0.113.0", "203.0.113.0"),
        ("10.1.2.255", "10.1.2.0"),
        ("2001:db8:abcd:12:1:2:3:4", "2001:db8:abcd::"),
        ("::1", "::"),
        ("::ffff:203.0.113.77", "203.0.113.0"),
    ],
)
def test_ip_e_truncado_em_24_bits_no_ipv4_e_48_no_ipv6(address: str, expected: str) -> None:
    assert truncate_client_address(address) == expected


@pytest.mark.parametrize("address", ["testclient", "", "300.1.1.1", "203.0.113.77:443"])
def test_endereco_que_nao_e_ip_nao_vai_para_o_evento(address: str) -> None:
    assert truncate_client_address(address) == "unknown"


def test_producao_sem_segredo_do_user_hash_nao_sobe() -> None:
    settings = Settings(environment="production", db_port=1, cursor_secret=SecretStr("c" * 32))

    with pytest.raises(MissingSecretError, match="GS_USER_HASH_SECRET"):
        create_app(settings)


def test_producao_usa_o_segredo_do_user_hash_configurado() -> None:
    settings = Settings(environment="production", user_hash_secret=SecretStr("u" * 32))

    assert settings.user_hash_key() == b"u" * 32


@pytest.mark.parametrize("environment", ["development", "test"])
def test_desenvolvimento_e_teste_tem_segredo_padrao_do_user_hash(environment: str) -> None:
    settings = Settings.model_validate({"environment": environment})

    assert len(settings.user_hash_key()) >= 32


def test_segredo_do_user_hash_curto_e_recusado() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", user_hash_secret=SecretStr("u" * 31))


# Status de erro dos spans do SQLAlchemy


SQLALCHEMY_SCOPE = "opentelemetry.instrumentation.sqlalchemy"
REDACTED_DB_ERROR = "database error (detail redacted)"


def _export_one(scope: str, status: Status | None) -> ReadableSpan:
    """Termina um span do `scope` pelo processador de produção e devolve o que foi exportado."""
    exporter = InMemorySpanExporter()
    processor = make_span_processor(exporter)
    assert processor is not None
    provider = TracerProvider()
    provider.add_span_processor(processor)
    span = provider.get_tracer(scope).start_span("SELECT goblinshift")
    if status is not None:
        span.set_status(status)
    span.set_attribute("db.statement", "INSERT INTO t (k) VALUES (%(k)s)")
    span.end()
    (exported,) = exporter.get_finished_spans()
    return exported


def test_erro_do_banco_sai_sem_os_valores_do_detail() -> None:
    # A instrumentação grava `str(exceção)`, que no psycopg traz `DETAIL: Key (k)=(<valor>)`.
    canary = f"CANARY-{UUID(int=7)}"
    message = (
        f'duplicate key value violates unique constraint "t_pkey"\nDETAIL:  Key (k)=({canary})'
    )

    exported = _export_one(SQLALCHEMY_SCOPE, Status(StatusCode.ERROR, message))

    assert exported.status.status_code is StatusCode.ERROR
    assert exported.status.description == REDACTED_DB_ERROR
    assert exported.attributes == {"db.statement": "INSERT INTO t (k) VALUES (%(k)s)"}


def test_consulta_sem_erro_do_banco_sai_inalterada() -> None:
    exported = _export_one(SQLALCHEMY_SCOPE, None)

    assert exported.status.status_code is StatusCode.UNSET
    assert exported.status.description is None


def test_erro_de_outra_instrumentacao_mantem_a_descricao() -> None:
    exported = _export_one("opentelemetry.instrumentation.asgi", Status(StatusCode.ERROR, "x"))

    assert exported.status.description == "x"


def test_sem_exportador_nem_endpoint_nao_ha_processador(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    assert make_span_processor(None) is None
