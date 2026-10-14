"""Segredo que assina os cursores da listagem (docs/GoblinShiftPlan.md, 1.6 e 3.6)."""

import pytest
from pydantic import SecretStr, ValidationError

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import MissingSecretError, Settings

SECRET = "s" * 32


def test_producao_sem_segredo_de_cursor_nao_sobe() -> None:
    with pytest.raises(MissingSecretError):
        create_app(Settings(environment="production", db_port=1))


def test_producao_usa_o_segredo_configurado() -> None:
    settings = Settings(environment="production", cursor_secret=SecretStr(SECRET))

    assert settings.cursor_signing_key() == SECRET.encode()


@pytest.mark.parametrize("environment", ["development", "test"])
def test_desenvolvimento_e_teste_tem_segredo_padrao(environment: str) -> None:
    settings = Settings.model_validate({"environment": environment})

    assert len(settings.cursor_signing_key()) >= 32


def test_segredo_curto_e_recusado() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", cursor_secret=SecretStr("s" * 31))
