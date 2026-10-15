"""O fuzzer não pode esgotar o limitador nem habilitar sua fábrica fora do teste local."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from goblinshift.identity.rate_limit import REQUESTS_PER_MINUTE, RequestClass
from goblinshift.platform.settings import Settings
from scripts import contract_server


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> dict[RequestClass, int]:
    original = REQUESTS_PER_MINUTE.copy()
    for key, value in original.items():
        monkeypatch.setitem(REQUESTS_PER_MINUTE, key, value)
    return original


@pytest.mark.parametrize(
    "update",
    [
        {"environment": "production"},
        {"environment": "development"},
        {"db_host": "database.example.com"},
        {"db_password_file": Path("/vps-secret")},
        {"runner": "executor_ctl"},
    ],
)
def test_recusa_antes_de_alterar_limites_ou_montar_api(
    update: dict[str, Any], monkeypatch: pytest.MonkeyPatch, limits: dict[RequestClass, int]
) -> None:
    settings = Settings(environment="test").model_copy(update=update)
    monkeypatch.setattr(contract_server, "Settings", lambda: settings)

    with pytest.raises(RuntimeError, match="exige ambiente test"):
        contract_server.create_app()

    assert limits == REQUESTS_PER_MINUTE


def test_so_a_fabrica_local_aumenta_o_orcamento(
    monkeypatch: pytest.MonkeyPatch, limits: dict[RequestClass, int]
) -> None:
    monkeypatch.setattr(contract_server, "Settings", lambda: Settings(environment="test"))

    app = contract_server.create_app()
    try:
        assert set(REQUESTS_PER_MINUTE.values()) == {contract_server.FUZZ_REQUEST_BUDGET}
        assert limits == {
            RequestClass.READ: 120,
            RequestClass.COMMAND: 60,
            RequestClass.RUN_CREATE: 10,
        }
        assert app.state.settings.environment == "test"
        assert app.dependency_overrides == {}
    finally:
        app.state.engine.dispose()


@pytest.mark.parametrize(
    "unsafe",
    [
        {"GS_ENVIRONMENT": "production"},
        {"GS_DB_HOST": "database.example.com"},
        {"GS_DB_PASSWORD_FILE": "/vps-secret"},
    ],
)
def test_script_recusa_antes_de_migrar_ou_iniciar_api(
    unsafe: dict[str, str], tmp_path: Path
) -> None:
    marker = tmp_path / "uv-called"
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/sh\n: > "$CONTRACT_MARKER"\nexit 99\n', encoding="utf-8")
    uv.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GS_")}
    env.update(unsafe)
    env.update(PATH=f"{tmp_path}:/usr/bin:/bin", CONTRACT_MARKER=str(marker))
    script = Path(__file__).resolve().parents[1] / "scripts" / "contract-test.sh"

    result = subprocess.run(  # noqa: S603 - script do próprio checkout, ambiente controlado.
        ["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 2
    assert "recusado" in result.stderr
    assert not marker.exists()
