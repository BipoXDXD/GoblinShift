"""Saturação da API com alerta, e limite por cliente com roteiro manual (README do OpenObserve).

O 503 do teto de concorrência do uvicorn sai antes do ASGI, sem span: a API conta a linha que o
uvicorn escreve e exporta a contagem por janela num span `api.saturation`, que o alerta
`gs-causa-api-saturada` lê. O 429 do `limit_req` é do Nginx, cujo log não chega ao OpenObserve, e
fica no roteiro manual, que conta as linhas no `docker logs`. Estes testes amarram a mensagem que o
contador procura ao que o uvicorn escreve de fato, o alerta ao span que a API exporta, o roteiro ao
que os programas escrevem e a premissa de que o log dos containers não chega: se ela cair, o lugar
do 429 também é um alerta.
"""

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from uvicorn.protocols.http import h11_impl, httptools_impl

from goblinshift.entrypoints.api.saturation import (
    REJECTED_ATTRIBUTE,
    SATURATION_SPAN,
    UVICORN_CONCURRENCY_MESSAGE,
)
from openobserve.catalog import DestinationKey, alerts
from tests.test_container_logs import uvicorn_cmd

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "deploy" / "openobserve" / "README.md"
NGINX_SITE = ROOT / "docker" / "nginx" / "templates" / "default.conf.template"
ACCESS_LOG_FORMAT = ROOT / "docker" / "nginx" / "templates" / "access-log.conf.template"
COLLECTOR_CONFIG = ROOT / "docker" / "otel-collector" / "config.yaml"
APPLICATION_SOURCE = ROOT / "src" / "goblinshift"
# O que exportaria o log da aplicação por OTLP: o exportador e a ponte do `logging` do SDK.
OTLP_LOG_EXPORT = re.compile(r"_log_exporter|LoggingHandler|LoggerProvider")
SATURATION_SECTION = "### Saturação da API e limite por cliente"
SATURATION_ALERT = "gs-causa-api-saturada"
# Níveis do uvicorn em que o aviso do teto ainda chega aos handlers do `uvicorn.error`.
LOG_LEVELS_WITH_WARNING = {"trace", "debug", "info", "warning"}
# Como cada campo sai no log de acesso do Nginx (JSON do formato `privacy`) para os valores que
# o roteiro conta: 429 do `limit_req` e 503 que veio da API.
NGINX_REJECTED = '"status":429'
# O 429 do próprio Nginx não passa pelo upstream, e o formato JSON escreve a variável vazia como
# "" (conferido no nginx 1.30.6 da imagem). O 429 da API (QUOTA_EXCEEDED, RATE_LIMITED das
# transferências) chega com "upstream_status":"429" e fica fora da contagem do `limit_req`.
NGINX_NO_UPSTREAM = '"upstream_status":""'
NGINX_UPSTREAM_UNAVAILABLE = '"upstream_status":"503"'


def _saturation_section() -> str:
    text = README.read_text(encoding="utf-8")
    assert SATURATION_SECTION in text, f"README sem a seção {SATURATION_SECTION!r}"
    section = text.split(SATURATION_SECTION, 1)[1]
    return re.split(r"\n#{1,3} ", section, maxsplit=1)[0]


def _shell_commands(section: str) -> list[str]:
    return [line for line in section.splitlines() if "docker logs" in line]


@pytest.mark.parametrize("module", [h11_impl, httptools_impl], ids=["h11", "httptools"])
def test_uvicorn_avisa_o_teto_com_a_mensagem_que_o_contador_procura(module: Any) -> None:
    # A mensagem não é API pública do uvicorn: uma atualização que a mude cala o alerta.
    assert f'"{UVICORN_CONCURRENCY_MESSAGE}"' in inspect.getsource(module)


def test_api_roda_num_nivel_de_log_que_entrega_o_aviso_do_teto() -> None:
    # Com `--log-level error` ou acima, o `uvicorn.error` descarta o WARNING antes do contador.
    cmd = uvicorn_cmd()

    level = cmd[cmd.index("--log-level") + 1] if "--log-level" in cmd else "info"
    assert level in LOG_LEVELS_WITH_WARNING


def test_saturacao_tem_alerta_urgente_lendo_o_span_da_api() -> None:
    by_name = {alert.name: alert for alert in alerts("default", "gs-")}

    alert = by_name[SATURATION_ALERT]
    assert alert.stream_type == "traces"
    assert alert.destination is DestinationKey.URGENT
    assert f"operation_name = '{SATURATION_SPAN}'" in alert.sql
    assert REJECTED_ATTRIBUTE.replace(".", "_") in alert.sql


def test_roteiro_aponta_o_alerta_e_ainda_conta_a_mensagem_no_log_da_api() -> None:
    section = _saturation_section()

    assert f"`{SATURATION_ALERT}`" in section
    assert any(
        "goblinshift-api-1" in command and UVICORN_CONCURRENCY_MESSAGE in command
        for command in _shell_commands(section)
    )


def test_roteiro_manda_conferir_as_conexoes_do_proxy_e_o_nginx_ainda_fecha_cada_uma() -> None:
    # O uvicorn compara o teto com as conexões abertas, e conexão keep-alive ociosa ocupa vaga.
    # O roteiro afirma que o upstream do Nginx não reaproveita conexões: se passar a reaproveitar,
    # a seção precisa dizer quanto do teto o pool ocioso do Nginx consome.
    section = _saturation_section()
    site = NGINX_SITE.read_text(encoding="utf-8")

    assert "keep-alive" in section and "sport = :8000" in section
    assert not re.search(r"^\s*keepalive\s", site, re.MULTILINE)
    assert "proxy_http_version 1.1" not in site


@pytest.mark.parametrize("needle", [NGINX_REJECTED, NGINX_UPSTREAM_UNAVAILABLE])
def test_roteiro_conta_os_campos_do_log_de_acesso_do_nginx(needle: str) -> None:
    commands = _shell_commands(_saturation_section())

    assert any("goblinshift-nginx-1" in command and needle in command for command in commands)


def test_roteiro_conta_so_o_429_do_limit_req_e_nao_o_da_api() -> None:
    commands = [
        command
        for command in _shell_commands(_saturation_section())
        if "goblinshift-nginx-1" in command and NGINX_REJECTED in command
    ]

    assert commands
    assert all(NGINX_NO_UPSTREAM in command for command in commands), commands


def test_log_de_acesso_do_nginx_escreve_os_campos_como_o_roteiro_procura() -> None:
    log_format = ACCESS_LOG_FORMAT.read_text(encoding="utf-8")

    assert '"status":$status' in log_format
    assert '"upstream_status":"$upstream_status"' in log_format


def _log_receivers(collector: dict[str, Any]) -> dict[str, list[str]]:
    """Receptores de cada pipeline de logs do Collector: `logs` e qualquer `logs/<nome>`."""
    pipelines: dict[str, Any] = collector["service"]["pipelines"]
    return {
        name: list(pipeline.get("receivers", []))
        for name, pipeline in pipelines.items()
        if name == "logs" or name.startswith("logs/")
    }


def test_pipeline_de_logs_com_nome_tambem_e_conferido() -> None:
    # Um `logs/containers` com `filelog` levaria o log dos containers ao OpenObserve sem tocar no
    # pipeline `logs`; a conferência abaixo precisa enxergá-lo.
    collector = {
        "service": {
            "pipelines": {
                "logs": {"receivers": ["otlp"]},
                "logs/containers": {"receivers": ["filelog"]},
                "traces": {"receivers": ["otlp"]},
            }
        }
    }

    assert _log_receivers(collector) == {"logs": ["otlp"], "logs/containers": ["filelog"]}


def test_logs_dos_containers_nao_chegam_ao_openobserve() -> None:
    # Premissa do roteiro manual do 429: se um receptor de arquivo (filelog) ou outro entrar em
    # algum pipeline de logs do Collector, o 429 também passa a ter uma causa no catálogo, e a
    # seção do README muda.
    collector = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))

    receivers = _log_receivers(collector)
    assert receivers
    assert all(names == ["otlp"] for names in receivers.values()), receivers
    exporting = [
        path
        for path in APPLICATION_SOURCE.rglob("*.py")
        if OTLP_LOG_EXPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert exporting == []
    assert "logs" not in {alert.stream_type for alert in alerts("default", "gs-")}
