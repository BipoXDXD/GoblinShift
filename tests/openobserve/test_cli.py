"""Linha de comando: `python -m openobserve provision|sli`, com segredos só em arquivo."""

from pathlib import Path

import pytest

from openobserve.__main__ import main
from openobserve.catalog import alerts
from openobserve.provision import declare_columns, provision
from tests.openobserve.conftest import CREDENTIALS, Telemetry, unique_stream
from tests.openobserve.test_provision import destination_url

PRODUCTION_PREFIX = "gs-"


def secrets_file(tmp_path: Path, mode: int = 0o600) -> Path:
    path = tmp_path / "alertas.env"
    path.write_text(
        f"OPENOBSERVE_ROOT_EMAIL={CREDENTIALS.email}\n"
        f"OPENOBSERVE_ROOT_PASSWORD={CREDENTIALS.password}\n"
        "GS_ALERT_URGENT_URL=https://ntfy.example.invalid/gs-urgente\n"
        "GS_ALERT_HEARTBEAT_URL=https://hc-ping.example.invalid/0000\n"
    )
    path.chmod(mode)
    return path


def test_sem_subcomando_mostra_o_uso(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2
    assert "provision" in capsys.readouterr().err


def test_nao_existe_opcao_de_senha_na_linha_de_comando(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["provision", "--help"])

    help_text = capsys.readouterr().out
    assert "--secrets-file" in help_text
    assert "password" not in help_text.lower()


def test_segredo_legivel_por_outros_sai_com_2_sem_falar_com_o_openobserve(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["provision", "--secrets-file", str(secrets_file(tmp_path, 0o644)), "--url", "http://x:1"]
    )

    assert code == 2
    assert "chmod 600" in capsys.readouterr().err


def test_openobserve_inacessivel_sai_com_1_e_mensagem_sem_senha(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["provision", "--secrets-file", str(secrets_file(tmp_path)), "--url", "http://127.0.0.1:9"]
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "127.0.0.1:9" in err
    assert CREDENTIALS.password not in err


@pytest.mark.openobserve
def test_provision_cria_os_alertas_de_producao_e_e_idempotente(
    telemetry: Telemetry, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stream = unique_stream("cli")
    url = telemetry.api.base_url
    args = ["provision", "--secrets-file", str(secrets_file(tmp_path)), "--url", url]
    try:
        first = main([*args, "--stream", stream])
        first_out = capsys.readouterr().out
        second = main([*args, "--stream", stream])
        second_out = capsys.readouterr().out

        assert (first, second) == (0, 0)
        expected = {a.name for a in alerts(stream=stream, prefix=PRODUCTION_PREFIX)}
        assert set(telemetry.api.alerts_named(PRODUCTION_PREFIX)) == expected
        assert "alerta criado: gs-slo-aceitacao-urgente" in first_out
        assert "alerta atualizado: gs-slo-aceitacao-urgente" in second_out
        assert "criado" not in second_out
        assert destination_url(telemetry, "gs-aviso") == "https://ntfy.example.invalid/gs-urgente"
    finally:
        provision(telemetry.api, alerts=[], destinations={}, prefix=PRODUCTION_PREFIX)


@pytest.mark.openobserve
def test_sli_mostra_os_tres_slos_da_janela(
    telemetry: Telemetry, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stream = unique_stream("relatorio")
    traces = telemetry.traces(stream)
    for status in (202, 202, 202, 500):
        traces.acceptance(status, error_code="INTERNAL_ERROR" if status == 500 else None)
    traces.flush()
    declare_columns(telemetry.api, stream)  # o que o provisionamento já fez na produção

    code = main(
        [
            "sli",
            "--secrets-file",
            str(secrets_file(tmp_path)),
            "--url",
            telemetry.api.base_url,
            "--stream",
            stream,
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "aceitacao: 3/4 bons, SLI 75.000% (meta 99.500%), orçamento restante -4900.0%" in out
    assert "execucao: sem eventos elegíveis na janela (meta 99.000%)" in out
    assert "download: sem eventos elegíveis na janela (meta 99.500%)" in out
    assert "ESGOTADO" in out
