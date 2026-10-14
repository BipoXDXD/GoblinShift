"""Peças puras do evento amplo (docs/GoblinShiftPlan.md, 3.7): hash da conta e IP truncado."""

from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import MissingSecretError, Settings
from goblinshift.platform.telemetry import UserHasher, truncate_client_address

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
