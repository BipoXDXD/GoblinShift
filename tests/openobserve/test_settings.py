"""Segredos do provisionamento: arquivo no formato .env, nunca linha de comando."""

import re
from pathlib import Path

import pytest

from openobserve.settings import ProvisionSettings, SettingsError

URGENT = "https://ntfy.sh/gs-urgente-0f3a"
HEARTBEAT = "https://hc-ping.com/7d2b8c1e-0000-4000-8000-000000000001"


def write_secrets(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text)
    path.chmod(mode)
    return path


def complete(extra: str = "") -> str:
    return (
        "# credenciais do OpenObserve\n"
        "OPENOBSERVE_ROOT_EMAIL=root@goblinshift.dev\n"
        "OPENOBSERVE_ROOT_PASSWORD=Senha#Forte1\n"
        "\n"
        f"GS_ALERT_URGENT_URL={URGENT}\n"
        f"GS_ALERT_HEARTBEAT_URL='{HEARTBEAT}'\n" + extra
    )


def test_le_credencial_e_destinos(tmp_path: Path) -> None:
    settings = ProvisionSettings.from_files([write_secrets(tmp_path / "a.env", complete())])

    assert settings.email == "root@goblinshift.dev"
    assert settings.password == "Senha#Forte1"  # noqa: S105 (valor de teste)
    assert settings.urgent_url == URGENT
    assert settings.heartbeat_url == HEARTBEAT


def test_aviso_sem_url_propria_vai_ao_destino_urgente(tmp_path: Path) -> None:
    settings = ProvisionSettings.from_files([write_secrets(tmp_path / "a.env", complete())])

    assert settings.warning_url == URGENT


def test_aviso_com_url_propria(tmp_path: Path) -> None:
    warning = "https://discord.com/api/webhooks/1/aviso"
    path = write_secrets(tmp_path / "a.env", complete(f"GS_ALERT_WARNING_URL={warning}\n"))

    assert ProvisionSettings.from_files([path]).warning_url == warning


def test_varios_arquivos_se_somam_e_o_ultimo_vence(tmp_path: Path) -> None:
    app_env = write_secrets(
        tmp_path / "app.env",
        "OPENOBSERVE_ROOT_EMAIL=velho@x.dev\nOPENOBSERVE_ROOT_PASSWORD=p\nOUTRA=1\n",
    )
    alerts_env = write_secrets(
        tmp_path / "alerts.env",
        f"OPENOBSERVE_ROOT_EMAIL=novo@x.dev\nGS_ALERT_URGENT_URL={URGENT}\n"
        f"GS_ALERT_HEARTBEAT_URL={HEARTBEAT}\n",
    )

    settings = ProvisionSettings.from_files([app_env, alerts_env])

    assert (settings.email, settings.password) == ("novo@x.dev", "p")


@pytest.mark.parametrize(
    "missing",
    [
        "OPENOBSERVE_ROOT_EMAIL",
        "OPENOBSERVE_ROOT_PASSWORD",
        "GS_ALERT_URGENT_URL",
        "GS_ALERT_HEARTBEAT_URL",
    ],
)
def test_falta_de_chave_obrigatoria_nomeia_a_chave(tmp_path: Path, missing: str) -> None:
    text = "".join(line + "\n" for line in complete().splitlines() if not line.startswith(missing))
    path = write_secrets(tmp_path / "a.env", text)

    with pytest.raises(SettingsError, match=missing):
        ProvisionSettings.from_files([path])


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660])
def test_arquivo_legivel_por_outros_e_recusado(tmp_path: Path, mode: int) -> None:
    path = write_secrets(tmp_path / "a.env", complete(), mode)

    with pytest.raises(SettingsError, match="chmod 600"):
        ProvisionSettings.from_files([path])


def test_arquivo_inexistente_e_erro_de_configuracao(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=re.escape("nao-existe.env")):
        ProvisionSettings.from_files([tmp_path / "nao-existe.env"])


@pytest.mark.parametrize(
    "url",
    ["http://ntfy.sh/topico", "ftp://x.dev/a", "ntfy.sh/topico", "https://", "https:///sem-host"],
)
def test_destino_que_nao_e_https_e_recusado_sem_mostrar_a_url(tmp_path: Path, url: str) -> None:
    text = complete().replace(f"GS_ALERT_URGENT_URL={URGENT}", f"GS_ALERT_URGENT_URL={url}")
    path = write_secrets(tmp_path / "a.env", text)

    with pytest.raises(SettingsError, match="GS_ALERT_URGENT_URL") as error:
        ProvisionSettings.from_files([path])
    # A URL do destino é segredo: quem tem a URL publica no canal.
    assert url not in str(error.value)


def test_linha_sem_igual_e_recusada_com_o_numero_da_linha(tmp_path: Path) -> None:
    path = write_secrets(tmp_path / "a.env", complete("LINHA_QUEBRADA\n"))

    with pytest.raises(SettingsError, match="linha 7"):
        ProvisionSettings.from_files([path])


def test_repr_nao_mostra_senha_nem_urls(tmp_path: Path) -> None:
    settings = ProvisionSettings.from_files([write_secrets(tmp_path / "a.env", complete())])

    text = repr(settings)
    for secret in ("Senha#Forte1", URGENT, HEARTBEAT):
        assert secret not in text
