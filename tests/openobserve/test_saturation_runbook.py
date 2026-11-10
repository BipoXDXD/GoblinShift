"""Saturação da API e limite por cliente: sem alerta, com roteiro manual (README do OpenObserve).

Os dois sinais existem só no log dos containers, que não chega ao OpenObserve: o 503 do teto de
concorrência do uvicorn sai antes do ASGI, sem span, e o 429 do `limit_req` é do Nginx. O roteiro
conta as linhas no `docker logs`. Estes testes amarram o texto que ele procura ao que os programas
escrevem de fato, e a premissa de que o log não chega: se ela cair, o lugar do sinal é um alerta.
"""

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from uvicorn.protocols.http import h11_impl, httptools_impl

from openobserve.catalog import alerts

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "deploy" / "openobserve" / "README.md"
ACCESS_LOG_FORMAT = ROOT / "docker" / "nginx" / "templates" / "access-log.conf.template"
COLLECTOR_CONFIG = ROOT / "docker" / "otel-collector" / "config.yaml"
APPLICATION_SOURCE = ROOT / "src" / "goblinshift"
# O que exportaria o log da aplicação por OTLP: o exportador e a ponte do `logging` do SDK.
OTLP_LOG_EXPORT = re.compile(r"_log_exporter|LoggingHandler|LoggerProvider")
SATURATION_SECTION = "### Saturação da API e limite por cliente"
UVICORN_SATURATION_MESSAGE = "Exceeded concurrency limit."
# Como cada campo sai no log de acesso do Nginx (JSON do formato `privacy`) para os valores que
# o roteiro conta: 429 do `limit_req` e 503 que veio da API.
NGINX_REJECTED = '"status":429'
NGINX_UPSTREAM_UNAVAILABLE = '"upstream_status":"503"'


def _saturation_section() -> str:
    text = README.read_text(encoding="utf-8")
    assert SATURATION_SECTION in text, f"README sem a seção {SATURATION_SECTION!r}"
    section = text.split(SATURATION_SECTION, 1)[1]
    return re.split(r"\n#{1,3} ", section, maxsplit=1)[0]


def _shell_commands(section: str) -> list[str]:
    return [line for line in section.splitlines() if "docker logs" in line]


@pytest.mark.parametrize("module", [h11_impl, httptools_impl], ids=["h11", "httptools"])
def test_uvicorn_avisa_o_teto_com_a_mensagem_do_roteiro(module: Any) -> None:
    # A mensagem não é API pública do uvicorn: uma atualização que a mude cala o roteiro.
    assert f'"{UVICORN_SATURATION_MESSAGE}"' in inspect.getsource(module)


def test_roteiro_conta_a_mensagem_do_teto_no_log_da_api() -> None:
    commands = _shell_commands(_saturation_section())

    assert any(
        "goblinshift-api-1" in command and UVICORN_SATURATION_MESSAGE in command
        for command in commands
    )


@pytest.mark.parametrize("needle", [NGINX_REJECTED, NGINX_UPSTREAM_UNAVAILABLE])
def test_roteiro_conta_os_campos_do_log_de_acesso_do_nginx(needle: str) -> None:
    commands = _shell_commands(_saturation_section())

    assert any("goblinshift-nginx-1" in command and needle in command for command in commands)


def test_log_de_acesso_do_nginx_escreve_os_campos_como_o_roteiro_procura() -> None:
    log_format = ACCESS_LOG_FORMAT.read_text(encoding="utf-8")

    assert '"status":$status' in log_format
    assert '"upstream_status":"$upstream_status"' in log_format


def test_logs_dos_containers_nao_chegam_ao_openobserve() -> None:
    # Se um receptor de arquivo (filelog) ou outro entrar no Collector, o roteiro manual deixa de
    # ser o lugar destes sinais: crie as causas no catálogo e reescreva a seção do README.
    pipelines = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))["service"]["pipelines"]

    assert pipelines["logs"]["receivers"] == ["otlp"]
    exporting = [
        path
        for path in APPLICATION_SOURCE.rglob("*.py")
        if OTLP_LOG_EXPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert exporting == []
    assert {alert.stream_type for alert in alerts("default", "gs-")} == {"traces", "metrics"}
